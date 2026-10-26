"""Test-process startup guard. Copied verbatim into each temporary service.

No connection or hook is installed merely by importing this module. The generated
sitecustomize calls install() before application, migrations or fixtures load.
"""
import builtins
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


class IsolationViolation(BaseException):
    """Not an Exception: fixtures must not turn an isolation failure into a skip."""


class DatabaseUnavailable(Exception):
    pass


def process_birth(pid):
    """Return an OS creation identity; never identify processes by name alone."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 5:
                raise OSError("Cannot query the recorded test process")
            return None
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise OSError("Cannot query process exit state")
            if code.value != 259:
                return None
            created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(system), ctypes.byref(user)):
                raise OSError("Cannot verify process creation identity")
            return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except FileNotFoundError:
            return None
        return stat[stat.rfind(")") + 2:].split()[19]
    raise OSError("Process identity verification is only supported on Windows and Linux")


def stop_process(pid, birth):
    """Terminate only a recorded PID with the same OS creation identity."""
    if pid == os.getpid() or not isinstance(pid, int) or pid <= 0 or not str(birth).isdigit():
        raise ValueError("Unsafe process cleanup identity")
    if process_birth(pid) != str(birth):
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x0001 | 0x1000, False, pid)
        if not handle:
            raise OSError("Cannot open the owned test process for cleanup")
        try:
            # Recheck after opening, guarding against PID reuse between calls.
            if process_birth(pid) != str(birth):
                return False
            if not kernel.TerminateProcess(handle, 86):
                raise OSError("Cannot terminate the owned test process")
        finally:
            kernel.CloseHandle(handle)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)
    return True


def inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


@contextmanager
def audit_lock(path):
    """Serialize append across independent Python processes and threads."""
    with open(str(path) + ".lock", "a+b") as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def event(policy, kind, **fields):
    row = {"kind": kind, "pid": os.getpid(), "nonce": policy["nonce"], **fields}
    payload = (json.dumps(row, sort_keys=True) + "\n").encode()
    with audit_lock(policy["events"]):
        descriptor = os.open(policy["events"], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)


def violation(policy, reason):
    event(policy, "violation", reason=reason)
    raise IsolationViolation(reason)


def validate_target(policy, params):
    """Validate effective libpq parameters, never a redacted/rendered URL."""
    expected = {"host": "127.0.0.1", "port": "55433", "dbname": policy["database"],
                "user": policy["role"]}
    if any(str(params.get(key, "")) != value for key, value in expected.items()):
        violation(policy, "SQL destination is outside this isolated service")
    if any(key in params for key in ("hostaddr", "service", "servicefile", "passfile")):
        violation(policy, "Alternative libpq routing or credential files are forbidden")
    digest = hashlib.sha256(str(params.get("password", "")).encode()).hexdigest()
    if digest != policy["password_digest"]:
        violation(policy, "SQL credentials do not belong to this test run")
    options = params.get("options", "")
    allowed = ("", "-c search_path=order_tests") if policy["database"] == "orders_db" else ("",)
    if options not in allowed:
        violation(policy, "Unexpected SQL connection options")


def checked_connect(policy, original, parser, conninfo="", **kwargs):
    driver_options = {key: kwargs[key] for key in (
        "autocommit", "prepare_threshold", "context", "row_factory", "cursor_factory") if key in kwargs}
    libpq = {key: value for key, value in kwargs.items() if key not in driver_options}
    try:
        params = parser(conninfo, **libpq)
    except Exception:
        violation(policy, "Unparseable SQL connection parameters")
    validate_target(policy, params)
    # Explicit password and SSL mode avoid ambient libpq credential discovery.
    params["sslmode"] = "disable"
    try:
        connection = original(**params, **driver_options)
    except Exception:
        raise DatabaseUnavailable("The isolated PostgreSQL connection is unavailable") from None
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('cluster_name'), current_database(), current_user")
            identity = cursor.fetchone()
        if tuple(identity or ()) != (policy["cluster"], policy["database"], policy["role"]):
            violation(policy, "PostgreSQL instance identity mismatch")
        connection.rollback()
    except BaseException:
        connection.close()
        raise
    event(policy, "sql_verified", database=policy["database"], service=policy["service"])
    return connection


def validate_spawn(policy, args, kwargs):
    """Existing backend subprocess tests only start this same Python interpreter."""
    if kwargs.get("shell") or not isinstance(args, (list, tuple)) or not args:
        violation(policy, "Shell or opaque subprocess launch is forbidden")
    if Path(args[0]).resolve() != Path(sys.executable).resolve():
        violation(policy, "Subprocess must use the guarded Python interpreter")
    for arg in args[1:]:
        if arg in ("-m", "-c") or not str(arg).startswith("-"):
            break
        if any(letter in str(arg)[1:] for letter in "SIE"):
            violation(policy, "Python startup isolation cannot be disabled")
    env = kwargs.get("env", os.environ)
    if not isinstance(env, dict) and env is not os.environ:
        violation(policy, "Subprocess environment must be explicit")
    python_path = env.get("PYTHONPATH", "").split(os.pathsep)
    if str(Path(policy["service_dir"]).resolve()) not in [str(Path(p).resolve()) for p in python_path if p]:
        violation(policy, "Subprocess lost its startup guard path")
    if not inside(kwargs.get("cwd") or os.getcwd(), policy["workspace"]):
        violation(policy, "Subprocess working directory is outside the test workspace")


def verify_installed(policy):
    import psycopg
    if (getattr(builtins, "_uce_guard_nonce", None) != policy["nonce"]
            or getattr(psycopg.connect, "_uce_guard_nonce", None) != policy["nonce"]
            or getattr(subprocess.Popen, "_uce_guard_nonce", None) != policy["nonce"]):
        violation(policy, "Startup guard is not installed")


def install(policy):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    if not inside(Path.cwd(), policy["workspace"]):
        violation(policy, "Python started outside the test workspace")
    # Do not let ambient libpq variables supplement an incomplete connection.
    for name in list(os.environ):
        if name.startswith("PG"):
            del os.environ[name]
    original = psycopg.connect

    def guarded(conninfo="", **kwargs):
        try:
            return checked_connect(policy, original, conninfo_to_dict, conninfo, **kwargs)
        except DatabaseUnavailable:
            violation(policy, "Isolated PostgreSQL unavailable; skipping tests is forbidden")

    guarded._uce_guard_nonce = policy["nonce"]
    psycopg.connect = guarded
    psycopg.Connection.connect = classmethod(lambda cls, conninfo="", **kw: guarded(conninfo, **kw))

    async def async_forbidden(*args, **kwargs):
        violation(policy, "Async SQL is outside the existing synchronous backend suites")

    psycopg.AsyncConnection.connect = classmethod(async_forbidden)
    original_popen = subprocess.Popen
    startup_digests = {name: hashlib.sha256((Path(policy["service_dir"]) / name).read_bytes()).hexdigest()
                       for name in ("sitecustomize.py", "isolation_guard.py", "guard-policy.json")}

    class GuardedPopen(original_popen):
        _uce_guard_nonce = policy["nonce"]

        def __init__(self, args, *positional, **kwargs):
            if positional:
                violation(policy, "Opaque positional subprocess options are forbidden")
            validate_spawn(policy, args, kwargs)
            for name, digest in startup_digests.items():
                if hashlib.sha256((Path(policy["service_dir"]) / name).read_bytes()).hexdigest() != digest:
                    violation(policy, "Subprocess startup guard files changed")
            token = uuid.uuid4().hex
            kwargs["env"] = dict(kwargs.get("env", os.environ), UCE_GUARD_SPAWN_TOKEN=token)
            super().__init__(args, **kwargs)
            event(policy, "spawn", child_pid=self.pid, birth=process_birth(self.pid), token=token)

    subprocess.Popen = GuardedPopen

    def audit(kind, args):
        if kind == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0]))
            if path.name == ".env" or path.name.startswith(".env."):
                violation(policy, "Environment file access is forbidden")
            if inside(path, Path(policy["repository"]) / "secrets"):
                violation(policy, "Platform key access is forbidden")
        if kind in ("os.system", "os.exec", "os.posix_spawn"):
            violation(policy, "Unsupervised process execution is forbidden")
        if kind == "socket.connect":
            address = args[1]
            # Permit Windows asyncio socketpairs and test HTTP servers on loopback.
            if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
                violation(policy, "Outbound network access is forbidden")
            if address[1] in (5432, 5433, 8001, 8003, 8004, 8005, 8006, 8080, 8090, 3000, 9090):
                violation(policy, "Connection to a platform port is forbidden")

    sys.addaudithook(audit)
    builtins._uce_guard_nonce = policy["nonce"]
    verify_installed(policy)
    event(policy, "startup", service=policy["service"], birth=process_birth(os.getpid()),
          token=os.environ.pop("UCE_GUARD_SPAWN_TOKEN", ""))
