"""Independent PostgreSQL backend validation; no Compose or application secrets.

Only prepare/run/cleanup mutate resources. Import, --help and harness unit tests
never invoke Docker, SQL, migrations, fixtures or backend suites.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

import isolation_guard

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
WORK_BASE = Path(tempfile.gettempdir()) / "uce-backend-isolated"
RESULT_BASE = HERE / "results"
IMAGE = "sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
PORT = 55433
PURPOSE = "uce-backend-isolated"
LABEL = "uce.backend.validation"
SPECS = {
    "inventory-service": ("INVENTORY", "inventory_db", "inventory_svc", 51),
    "payment-service": ("PAYMENT", "payments_db", "payment_svc", 41),
    "order-service": ("ORDER", "orders_db", "order_svc", 281),
    "user-service": ("USER", "users_db", "user_svc", 87),
    "product-service": ("PRODUCT", "products_db", "product_svc", 33),
    "notification-service": ("NOTIFICATION", "notifications_db", "notification_svc", 82),
}
SAFE_ENV = ("PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SystemDrive",
            "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA")
CONTAINER_FORMAT = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
                    '"labels":{{json .Config.Labels}},"mounts":{{json .Mounts}},'
                    '"networks":{{json .NetworkSettings.Networks}},'
                    '"ports":{{json .HostConfig.PortBindings}},"memory":{{json .HostConfig.Memory}},'
                    '"swap":{{json .HostConfig.MemorySwap}},"host_mounts":{{json .HostConfig.Mounts}},'
                    '"cpus":{{json .HostConfig.NanoCpus}},"lifecycle":{{json .State}}}')
NETWORK_FORMAT = ('{"id":{{json .Id}},"name":{{json .Name}},"driver":{{json .Driver}},'
                  '"labels":{{json .Labels}},"containers":{{json .Containers}}}')


class HarnessError(Exception):
    pass


def clean_environment():
    return {name: os.environ[name] for name in SAFE_ENV if name in os.environ}


def within(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def reject_links(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise HarnessError("Symlinks and junctions are forbidden in harness paths")


def run_paths(run_id):
    if not re.fullmatch(r"backend-[0-9]{8}-[0-9]{6}(?:-[a-f0-9]{8})?", run_id):
        raise HarnessError("Run ID must be backend-YYYYMMDD-HHMMSS, optionally with an 8-digit hex suffix")
    workspace, results = WORK_BASE / run_id, RESULT_BASE / run_id
    reject_links(workspace)
    reject_links(results)
    if not within(workspace, WORK_BASE) or not within(results, RESULT_BASE):
        raise HarnessError("Run paths escaped their fixed roots")
    return workspace, results


def write_json(path, value):
    path = Path(path)
    reject_links(path)
    staging = path.with_suffix(path.suffix + ".tmp")
    with staging.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
    os.replace(staging, path)


def read_manifest(run_id):
    workspace, results = run_paths(run_id)
    path = workspace / "manifest.json"
    reject_links(path)
    if not path.is_file():
        raise HarnessError("No owned workspace manifest exists for this run")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("run_id") != run_id or manifest.get("purpose") != PURPOSE
            or manifest.get("workspace") != str(workspace.resolve())
            or manifest.get("results") != str(results.resolve())
            or not re.fullmatch(r"[a-f0-9]{32}", manifest.get("nonce", ""))):
        raise HarnessError("Invalid ownership manifest")
    if manifest.get("image") != IMAGE or manifest.get("port") != PORT:
        raise HarnessError("Manifest changed its fixed SQL destination or image")
    if (manifest.get("container_name") != "uce-backend-pg-" + run_id
            or manifest.get("network_name") != "uce-backend-net-" + run_id
            or manifest.get("cluster") != "uce-test-" + manifest["nonce"]):
        raise HarnessError("Manifest resource names or SQL identity changed")
    return manifest


def save_manifest(manifest):
    workspace, _ = run_paths(manifest["run_id"])
    write_json(workspace / "manifest.json", manifest)


@contextmanager
def action_lock(run_id):
    workspace, _ = run_paths(run_id)
    if not workspace.exists():
        yield  # cleanup may be idempotent; other actions will reject the missing manifest.
        return
    manifest = read_manifest(run_id)
    path = workspace / "action.lock"
    reject_links(path)
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("nonce") != manifest["nonce"]:
            raise HarnessError("Action lock ownership mismatch")
        if isolation_guard.process_birth(old["pid"]) == old["birth"]:
            raise HarnessError("Another action for this run is still active")
        path.unlink()  # This exact owned lock belongs to an exited/reused PID.
    token = uuid.uuid4().hex
    lock = {"pid": os.getpid(), "birth": isolation_guard.process_birth(os.getpid()),
            "nonce": manifest["nonce"], "token": token}
    with path.open("x", encoding="utf-8") as output:
        json.dump(lock, output)
    try:
        yield
    finally:
        if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("token") == token:
            path.unlink()


def redact(text, credentials):
    for value in credentials.values():
        if isinstance(value, str) and len(value) >= 16:
            text = text.replace(value, "[REDACTED_TEST_SECRET]")
    return text


class Docker:
    def __init__(self, results=None, credentials=None):
        self.results = Path(results) if results else None
        self.credentials = credentials or {}

    def call(self, *args, environment=None, timeout=30, absent_ok=False):
        # No shell, Compose, environment dumps or unrestricted inspect output.
        try:
            result = subprocess.run(["docker", *args], env=environment or clean_environment(),
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            raise HarnessError("Docker command unavailable; no further operation permitted") from None
        if self.results:
            with (self.results / "docker.log").open("a", encoding="utf-8") as output:
                output.write(redact(json.dumps(list(args)) + "\n" + result.stdout + result.stderr, self.credentials))
        if result.returncode:
            # Missing resources are distinguished by an exact ID inspection, never name patterns.
            target = re.escape(str(args[-1]))
            missing = re.search(r"No such (?:object|container|network):\s*" + target + r"(?:\s|$)|network\s+"
                                + target + r"\s+not found", result.stderr)
            if absent_ok and missing:
                return None
            raise HarnessError("Docker operation failed; consult the redacted run log")
        return result.stdout.strip()


def resource_id(value):
    if not re.fullmatch(r"[a-f0-9]{64}", value or ""):
        raise HarnessError("Docker did not return a full resource ID")
    return value


def labels(manifest):
    return {LABEL: PURPOSE, LABEL + ".run": manifest["run_id"], LABEL + ".nonce": manifest["nonce"]}


def verify_owner(resource, manifest, kind):
    expected_id = manifest.get(kind + "_id")
    expected_name = manifest.get(kind + "_name")
    if (resource.get("id") != resource_id(expected_id)
            or resource.get("name", "").lstrip("/") != expected_name
            or any((resource.get("labels") or {}).get(k) != v for k, v in labels(manifest).items())):
        raise HarnessError("Resource identity or ownership does not match this run")


def inspect_owned(docker, manifest, kind, absent_ok=False):
    identity = resource_id(manifest.get(kind + "_id"))
    args = ("inspect", "--format", CONTAINER_FORMAT, identity) if kind == "container" else (
        "network", "inspect", "--format", NETWORK_FORMAT, identity)
    raw = docker.call(*args, absent_ok=absent_ok)
    if raw is None:
        return None
    resource = json.loads(raw)
    if kind == "container" and "lifecycle" in resource:
        lifecycle = resource.pop("lifecycle") or {}
        resource["state"] = lifecycle.get("Status")
        resource["health"] = (lifecycle.get("Health") or {}).get("Status")
    verify_owner(resource, manifest, kind)
    return resource


def verify_container(resource, manifest):
    verify_owner(resource, manifest, "container")
    expected_ports = {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(PORT)}]}
    mounts = resource.get("mounts") or []
    expected_init = str((Path(manifest["workspace"]) / "init").resolve())
    valid_mounts = (len(mounts) == 2
                    and any(m.get("Type") == "tmpfs" and m.get("Destination") == "/var/lib/postgresql/data"
                            for m in mounts)
                    and any(m.get("Type") == "bind" and m.get("Destination") == "/docker-entrypoint-initdb.d"
                            and m.get("RW") is False and same_bind_source(m.get("Source", ""), expected_init)
                            for m in mounts))
    declared_tmpfs = [m for m in resource.get("host_mounts", [])
                      if m.get("Type") == "tmpfs" and m.get("Target") == "/var/lib/postgresql/data"]
    if (resource.get("image") != IMAGE or resource.get("ports") != expected_ports
            or set(resource.get("networks") or {}) != {manifest["network_name"]}
            or resource.get("memory") != 512 * 1024 ** 2 or resource.get("cpus") != 1_000_000_000
            or resource.get("swap") != 512 * 1024 ** 2
            or len(declared_tmpfs) != 1 or declared_tmpfs[0].get("TmpfsOptions", {}).get("SizeBytes") != 256 * 1024 ** 2
            or resource.get("state") != "running" or not valid_mounts):
        raise HarnessError("Container layout does not match the isolated PostgreSQL contract")
    endpoint = resource["networks"][manifest["network_name"]]
    if endpoint.get("NetworkID") != manifest["network_id"]:
        raise HarnessError("Container is attached to a different network identity")


def same_bind_source(actual, expected):
    actual, expected = str(actual).replace("\\", "/"), str(expected).replace("\\", "/")
    aliases = {expected.lower()}
    if re.match(r"^[A-Za-z]:/", expected):
        suffix = expected[0].lower() + expected[2:]
        aliases.update({"/run/desktop/mnt/host/" + suffix.lower(), "/host_mnt/" + suffix.lower()})
    return actual.lower() in aliases


def verify_network(resource, manifest, with_container=False):
    verify_owner(resource, manifest, "network")
    endpoints = set((resource.get("containers") or {}).keys())
    expected = {manifest["container_id"]} if with_container else set()
    if resource.get("driver") != "bridge" or endpoints != expected:
        raise HarnessError("Test network has an unexpected driver or container")


def container_args(manifest):
    args = ["run", "--detach", "--pull", "never", "--name", manifest["container_name"],
            "--network", manifest["network_name"], "--publish", f"127.0.0.1:{PORT}:5432",
            "--memory", "512m", "--memory-swap", "512m", "--cpus", "1", "--restart", "no",
            "--health-cmd", "pg_isready -h 127.0.0.1 -U uce_test_admin -d postgres",
            "--health-interval", "1s", "--health-timeout", "3s", "--health-retries", "60",
            "--mount", "type=tmpfs,destination=/var/lib/postgresql/data,tmpfs-size=268435456",
            "--mount", f"type=bind,source={Path(manifest['workspace']) / 'init'},destination=/docker-entrypoint-initdb.d,readonly"]
    for key, value in labels(manifest).items():
        args += ["--label", key + "=" + value]
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRES_INITDB_ARGS"):
        args += ["--env", key]
    for prefix, _, _, _ in SPECS.values():
        for suffix in ("NAME", "USER", "PASSWORD"):
            args += ["--env", prefix + "_DB_" + suffix]
    return args + [IMAGE, "-c", "cluster_name=" + manifest["cluster"],
                   "-c", "shared_buffers=32MB", "-c", "max_connections=50", "-c", "work_mem=2MB"]


def child_environment(manifest, service, password):
    _, database, role, _ = SPECS[service]
    workspace = Path(manifest["workspace"])
    source = workspace / "sources" / service
    environment = clean_environment()
    environment.update(DB_HOST="127.0.0.1", DB_PORT=str(PORT), DB_NAME=database, DB_USER=role,
                       DB_PASSWORD=password, INTERNAL_API_KEY="isolated-test-internal-key",
                       PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
                       PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONUTF8="1",
                       TEMP=str(workspace / "tmp"), TMP=str(workspace / "tmp"))
    if service == "order-service":
        environment["DB_SCHEMA"] = "order_tests"
    if service == "payment-service":
        environment["PAYMENT_SIMULATION_ENABLED"] = "true"
    return environment


def policy_for(manifest, service, password):
    _, database, role, _ = SPECS[service]
    return {"nonce": manifest["nonce"], "service": service, "database": database, "role": role,
            "cluster": manifest["cluster"], "password_digest": hashlib.sha256(password.encode()).hexdigest(),
            "workspace": manifest["workspace"], "repository": str(REPOSITORY),
            "service_dir": str(Path(manifest["workspace"]) / "sources" / service),
            "events": str(Path(manifest["workspace"]) / "guard-events.jsonl")}


def source_files(service):
    root = REPOSITORY / "backend" / service
    files = [root / "alembic.ini"]
    for folder in ("app", "tests", "alembic"):
        files.extend(p for p in (root / folder).rglob("*")
                     if p.is_file() and p.suffix in (".py", ".mako") and "__pycache__" not in p.parts)
    for path in files:
        reject_links(path)
        if not within(path, root) or not path.is_file():
            raise HarnessError("Invalid source copy input")
    return root, files


def copy_sources(manifest, credentials):
    hashes = {}
    for service in SPECS:
        original, files = source_files(service)
        destination = Path(manifest["workspace"]) / "sources" / service
        destination.mkdir(parents=True)
        for path in files:
            relative = path.relative_to(original)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            data = path.read_bytes()
            target.write_bytes(data)
            hashes[str(path.relative_to(REPOSITORY))] = hashlib.sha256(data).hexdigest()
        for original_name, copied_name in (("isolation_guard.py", "isolation_guard.py"),
                                           ("harness_child.py", "_uce_backend_child.py")):
            shutil.copyfile(HERE / original_name, destination / copied_name)
        write_json(destination / "guard-policy.json", policy_for(manifest, service, credentials[service]))
        # Python normally swallows sitecustomize errors. Exit immediately instead.
        (destination / "sitecustomize.py").write_text(startup_source(), encoding="utf-8")
    write_json(Path(manifest["workspace"]) / "source-hashes.json", hashes)


def startup_source():
    return ("import os, sys, json\nfrom pathlib import Path\ntry:\n"
            "    import isolation_guard\n"
            "    policy = json.loads(Path(__file__).with_name('guard-policy.json').read_text(encoding='utf-8'))\n"
            "    isolation_guard.install(policy)\nexcept BaseException:\n"
            "    sys.stderr.write('ISOLATION STARTUP FAILED\\n')\n    os._exit(86)\n")


def seal_support(manifest):
    workspace = Path(manifest["workspace"])
    hashes = {}
    for service in SPECS:
        for name in ("sitecustomize.py", "isolation_guard.py", "_uce_backend_child.py", "guard-policy.json"):
            path = workspace / "sources" / service / name
            hashes[str(path.relative_to(workspace))] = hashlib.sha256(path.read_bytes()).hexdigest()
    path = workspace / "init/01-init-databases.sh"
    hashes[str(path.relative_to(workspace))] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(workspace / "support-hashes.json", hashes)


def verify_sources(manifest):
    workspace = Path(manifest["workspace"])
    hashes = json.loads((workspace / "source-hashes.json").read_text(encoding="utf-8"))
    for relative, digest in hashes.items():
        path = REPOSITORY / relative
        reject_links(path)
        if not within(path, REPOSITORY / "backend"):
            raise HarnessError("Source hash path escaped backend")
        parts = Path(relative).parts
        copied = workspace / "sources" / parts[1] / Path(*parts[2:])
        if any(hashlib.sha256(p.read_bytes()).hexdigest() != digest for p in (path, copied)):
            raise HarnessError("Original or copied source changed during validation")
    support = json.loads((workspace / "support-hashes.json").read_text(encoding="utf-8"))
    for relative, digest in support.items():
        copied = workspace / relative
        reject_links(copied)
        if not within(copied, workspace) or hashlib.sha256(copied.read_bytes()).hexdigest() != digest:
            raise HarnessError("A guard or policy file changed during validation")


def snapshot(docker):
    raw = docker.call("ps", "-a", "--no-trunc", "--format", "{{json .}}")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    snapshot_rows = {}
    for row in rows:
        entry = {key: row.get(key) for key in ("Names", "Image", "State", "Status")}
        status = str(row.get("Status", ""))
        entry["Health"] = ("unhealthy" if "(unhealthy)" in status else "healthy" if "(healthy)" in status
                           else "starting" if "health: starting" in status else "N/A")
        exited = re.search(r"Exited \((\d+)\)", status)
        entry["ExitCode"] = int(exited.group(1)) if exited else None
        snapshot_rows[row["ID"]] = entry
    return snapshot_rows


def prepare(args):
    if args.port != PORT or args.image != IMAGE or args.memory != "512m" or args.tmpfs_size != "256m" or args.cpus != 1:
        raise HarnessError("The approved port, image and resource limits are fixed")
    workspace, results = run_paths(args.run_id)
    if args.results_dir and Path(args.results_dir).resolve() != results.resolve():
        raise HarnessError("Results must stay under tests/backend/results/<run-id>")
    if workspace.exists() or results.exists():
        raise HarnessError("Run ID already exists; results are never overwritten")
    for service in SPECS:
        python = REPOSITORY / "backend" / service / ".venv" / "Scripts" / "python.exe"
        if not python.is_file():
            raise HarnessError("An existing service virtual environment is missing")
    with socket.socket() as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", PORT))
    docker = Docker()
    if docker.call("image", "inspect", IMAGE, "--format", "{{.Id}}") != IMAGE:
        raise HarnessError("The approved local PostgreSQL image is unavailable")
    before = snapshot(docker)
    if any(row["Health"] in ("unhealthy", "starting") for row in before.values()):
        raise HarnessError("An existing container is not healthy; no resources were created")
    if any("k6" in str(row.get("Image", "")).lower() and row.get("State") == "running" for row in before.values()):
        raise HarnessError("An active k6 container prevents backend validation")
    workspace.mkdir(parents=True, mode=0o700)
    results.mkdir(parents=True)
    (workspace / "tmp").mkdir()
    (workspace / "init").mkdir()
    nonce = uuid.uuid4().hex
    manifest = {"purpose": PURPOSE, "run_id": args.run_id, "nonce": nonce,
                "workspace": str(workspace.resolve()), "results": str(results.resolve()),
                "container_name": "uce-backend-pg-" + args.run_id,
                "network_name": "uce-backend-net-" + args.run_id, "image": IMAGE, "port": PORT,
                "cluster": "uce-test-" + nonce, "before": before, "services": {}, "ready": False}
    manifest["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    save_manifest(manifest)
    credentials = {service: secrets.token_hex(24) for service in SPECS}
    credentials["admin"] = secrets.token_hex(24)
    # Only disposable test credentials exist here; never put them in the repository.
    secret_file = workspace / "test-credentials.json"
    secret_file.write_text(json.dumps(credentials), encoding="utf-8")
    secret_file.chmod(0o600)
    copy_sources(manifest, credentials)
    shutil.copyfile(REPOSITORY / "infra/postgres/init/01-init-databases.sh",
                    workspace / "init/01-init-databases.sh")
    seal_support(manifest)
    environment = clean_environment()
    environment.update(POSTGRES_USER="uce_test_admin", POSTGRES_PASSWORD=credentials["admin"],
                       POSTGRES_DB="postgres", POSTGRES_INITDB_ARGS="--auth-host=scram-sha-256")
    for service, (prefix, database, role, _) in SPECS.items():
        environment.update({prefix + "_DB_NAME": database, prefix + "_DB_USER": role,
                            prefix + "_DB_PASSWORD": credentials[service]})
    docker = Docker(results, credentials)
    network_args = ["network", "create", "--driver", "bridge"]
    for key, value in labels(manifest).items():
        network_args += ["--label", key + "=" + value]
    manifest["network_requested"] = True
    save_manifest(manifest)
    manifest["network_id"] = resource_id(docker.call(*network_args, manifest["network_name"]))
    save_manifest(manifest)
    verify_network(inspect_owned(docker, manifest, "network"), manifest)
    manifest["container_requested"] = True
    save_manifest(manifest)
    manifest["container_id"] = resource_id(docker.call(*container_args(manifest), environment=environment, timeout=60))
    save_manifest(manifest)
    verify_container(inspect_owned(docker, manifest, "container"), manifest)
    verify_network(inspect_owned(docker, manifest, "network"), manifest, with_container=True)
    deadline = time.monotonic() + 60
    while True:
        resource = inspect_owned(docker, manifest, "container")
        verify_container(resource, manifest)
        if resource.get("health") == "healthy":
            break
        if resource.get("health") not in (None, "starting") or time.monotonic() >= deadline:
            raise HarnessError("Temporary PostgreSQL did not become healthy")
        time.sleep(1)
    # A guarded child must verify all six SQL identities; readiness never trusts pg_isready alone.
    for service in SPECS:
        verify_sources(manifest)
        if run_child(manifest, credentials, service, "probe", probe=True):
            raise HarnessError("Isolated SQL preflight failed; no suites were executed")
    manifest["ready"] = True
    manifest["prepared_at_utc"] = datetime.now(timezone.utc).isoformat()
    save_manifest(manifest)
    write_json(results / "prepared.json", {k: v for k, v in manifest.items() if k != "before"})
    print(json.dumps({"prepared": True, "run_id": args.run_id, "port": PORT}))


def run_child(manifest, credentials, service, mode, probe=False):
    workspace, results = Path(manifest["workspace"]), Path(manifest["results"])
    source = workspace / "sources" / service
    python = REPOSITORY / "backend" / service / ".venv/Scripts/python.exe"
    if probe:
        # TCP health is already ready; a SQL mismatch is always fatal, never retried.
        code = ("import json,os; from pathlib import Path; import isolation_guard as g; "
                "import psycopg; from psycopg.conninfo import conninfo_to_dict; "
                "p=json.loads(Path('guard-policy.json').read_text()); "
                "g.verify_installed(p); "
                "c=psycopg.connect(host='127.0.0.1',port=55433,dbname=p['database'],user=p['role'],"
                "password=os.environ['DB_PASSWORD'],connect_timeout=2); c.close(); "
                "import sys,importlib.metadata as md; "
                "print(json.dumps({'python':sys.version.split()[0], 'packages':{n:md.version(n) "
                "for n in ('pytest','SQLAlchemy','psycopg','alembic')}}))")
        command = [str(python), "-B", "-c", code]
    else:
        command = [str(python), "-B", "_uce_backend_child.py", mode]
        if mode == "test":
            command += [str(results / (service + ".xml")), str(workspace / "tmp" / (service + "-pytest"))]
    log_path = results / (service + "-" + mode + ".log")
    timed_out = False
    with log_path.open("ab") as output:
        try:
            result = subprocess.run(command, cwd=source, env=child_environment(manifest, service, credentials[service]),
                                    stdout=output, stderr=subprocess.STDOUT, timeout=900)
        except subprocess.TimeoutExpired:
            timed_out = True
    # Redact synthetic credentials before retaining logs for the academic report.
    content = log_path.read_text(encoding="utf-8", errors="replace")
    log_path.write_text(redact(content, credentials), encoding="utf-8")
    if timed_out:
        stop_test_processes(manifest)
        raise HarnessError("A guarded child timed out; only recorded test processes were stopped")
    return result.returncode


def junit_result(path, expected):
    if not Path(path).is_file():
        raise HarnessError("JUnit report is missing")
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    counts = {"tests": len(cases), "failures": sum(c.find("failure") is not None for c in cases),
              "errors": sum(c.find("error") is not None for c in cases),
              "skipped": sum(c.find("skipped") is not None for c in cases)}
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    counts["accepted"] = counts["tests"] == expected and not any(counts[k] for k in ("failures", "errors", "skipped"))
    return counts


def guard_events(manifest):
    path = Path(manifest["workspace"]) / "guard-events.jsonl"
    if not path.is_file():
        raise HarnessError("Guard audit is missing")
    try:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (ValueError, OSError):
        raise HarnessError("Guard audit is incomplete") from None
    starts = {e.get("token") for e in events if e["kind"] == "startup" and e.get("token")}
    children = {e["token"] for e in events if e["kind"] == "spawn"}
    if (not any(e["kind"] == "startup" for e in events)
            or any(e.get("nonce") != manifest["nonce"] or e["kind"] == "violation" for e in events)
            or not children <= starts):
        raise HarnessError("Isolation violation or unguarded subprocess detected")
    return events


def stop_test_processes(manifest):
    path = Path(manifest["workspace"]) / "guard-events.jsonl"
    if not path.is_file():
        return
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if any(row.get("nonce") != manifest["nonce"] for row in events):
        raise HarnessError("Process audit ownership mismatch; refusing termination")
    for row in reversed(events):
        if row.get("kind") in ("startup", "spawn") and row.get("birth"):
            pid = row["child_pid"] if row["kind"] == "spawn" else row["pid"]
            isolation_guard.stop_process(pid, row["birth"])


def run_services(args):
    manifest = read_manifest(args.run_id)
    if not manifest.get("ready"):
        raise HarnessError("Preparation has not completed")
    if len(set(args.services)) != len(args.services):
        raise HarnessError("Duplicate service selection is forbidden")
    credentials_file = Path(manifest["workspace"]) / "test-credentials.json"
    reject_links(credentials_file)
    credentials = json.loads(credentials_file.read_text())
    docker = Docker(manifest["results"], credentials)
    verify_container(inspect_owned(docker, manifest, "container"), manifest)
    verify_network(inspect_owned(docker, manifest, "network"), manifest, with_container=True)
    verify_sources(manifest)
    for service in args.services:
        if service in manifest["services"]:
            raise HarnessError("Service already attempted; choose a new run ID rather than overwrite evidence")
        started = datetime.now(timezone.utc).isoformat()
        manifest["services"][service] = {"state": "started", "started_at_utc": started}
        save_manifest(manifest)
        verify_sources(manifest)
        if service != "order-service":
            if run_child(manifest, credentials, service, "migrate"):
                raise HarnessError("Migration failed; backend suite was not started")
            verify_sources(manifest)
        code = run_child(manifest, credentials, service, "test")
        verify_sources(manifest)
        xml = Path(manifest["results"]) / (service + ".xml")
        if xml.is_file():
            xml.write_text(redact(xml.read_text(encoding="utf-8"), credentials), encoding="utf-8")
        counts = junit_result(xml, SPECS[service][3])
        events = guard_events(manifest)
        if not any(e["kind"] == "sql_verified" and e.get("service") == service for e in events):
            raise HarnessError("No verified SQL connection was audited for this service")
        counts.update(exit_code=code, state="completed", started_at_utc=started,
                      finished_at_utc=datetime.now(timezone.utc).isoformat())
        manifest["services"][service] = counts
        save_manifest(manifest)
        write_json(Path(manifest["results"]) / "summary.json", manifest["services"])
        print(json.dumps({"service": service, **counts}))
        if code or not counts["accepted"]:
            raise HarnessError("Backend validation failed or skipped cases; no automatic retry")
    verify_sources(manifest)


def safe_delete_workspace(manifest):
    workspace, _ = run_paths(manifest["run_id"])
    current = read_manifest(manifest["run_id"])
    if current["nonce"] != manifest["nonce"] or workspace.resolve() == WORK_BASE.resolve():
        raise HarnessError("Workspace ownership changed")
    reject_links(workspace)
    shutil.rmtree(workspace)


def recover_creation(docker, manifest, kind):
    """Recover a lost create response by one exact name plus the unique nonce."""
    if manifest.get(kind + "_id") or not manifest.get(kind + "_requested"):
        return
    name = manifest[kind + "_name"]
    args = ("inspect", "--format", CONTAINER_FORMAT, name) if kind == "container" else (
        "network", "inspect", "--format", NETWORK_FORMAT, name)
    raw = docker.call(*args, absent_ok=True)
    if raw is None:
        return
    candidate = json.loads(raw)
    trial = dict(manifest, **{kind + "_id": resource_id(candidate.get("id"))})
    verify_owner(candidate, trial, kind)
    manifest[kind + "_id"] = trial[kind + "_id"]
    save_manifest(manifest)


def cleanup(args):
    workspace, results = run_paths(args.run_id)
    if not workspace.exists():
        if (results / "cleanup.json").is_file():
            print(json.dumps({"already_cleaned": True, "run_id": args.run_id}))
            return
        raise HarnessError("No ownership record; nothing will be removed")
    manifest = read_manifest(args.run_id)
    docker = Docker(results)
    recover_creation(docker, manifest, "container")
    recover_creation(docker, manifest, "network")
    # Fixtures normally close children; interrupted runs use exact PID + creation identity.
    stop_test_processes(manifest)
    audit = workspace / "guard-events.jsonl"
    if audit.is_file():
        shutil.copyfile(audit, results / "guard-events.jsonl")
    for name in ("source-hashes.json", "support-hashes.json"):
        if (workspace / name).is_file():
            shutil.copyfile(workspace / name, results / name)
    write_json(results / "execution.json", manifest)
    if manifest.get("container_id"):
        resource = inspect_owned(docker, manifest, "container", absent_ok=True)
        if resource:
            docker.call("rm", "--force", resource_id(manifest["container_id"]))
    if manifest.get("network_id"):
        resource = inspect_owned(docker, manifest, "network", absent_ok=True)
        if resource:
            verify_network(resource, manifest)
            docker.call("network", "rm", resource_id(manifest["network_id"]))
    after = snapshot(docker)
    before = manifest["before"]
    # Compare identity/state, not human uptime strings which naturally change.
    unchanged = all(identity in after and all(after[identity].get(k) == row.get(k)
                    for k in ("Names", "Image", "State", "Health", "ExitCode")) for identity, row in before.items())
    write_json(results / "cleanup.json", {"run_id": args.run_id, "temporary_resources_removed": True,
                                         "original_container_identity_state_unchanged": unchanged,
                                         "all_six_suites_accepted": set(manifest["services"]) == set(SPECS)
                                         and all(row.get("accepted") and row.get("exit_code") == 0
                                                 for row in manifest["services"].values()),
                                         "services": manifest["services"], "after": after})
    safe_delete_workspace(manifest)
    if not unchanged:
        raise HarnessError("Original container identity/state changed externally; inspect the final report")
    print(json.dumps({"cleaned": True, "run_id": args.run_id, "original_containers_unchanged": True}))


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="action", required=True)
    prepare_parser = commands.add_parser("prepare", help="Create the independently authorized disposable PostgreSQL")
    prepare_parser.add_argument("--run-id", required=True)
    prepare_parser.add_argument("--port", type=int, default=PORT)
    prepare_parser.add_argument("--image", default=IMAGE)
    prepare_parser.add_argument("--memory", default="512m")
    prepare_parser.add_argument("--tmpfs-size", default="256m")
    prepare_parser.add_argument("--cpus", type=int, default=1)
    prepare_parser.add_argument("--results-dir")
    run_parser = commands.add_parser("run", help="Run unchanged suites against verified temporary SQL only")
    run_parser.add_argument("--run-id", required=True)
    run_parser.add_argument("--services", nargs="+", choices=tuple(SPECS), required=True)
    cleanup_parser = commands.add_parser("cleanup", help="Remove only resources recorded with exact IDs and ownership")
    cleanup_parser.add_argument("--run-id", required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == "prepare":
            prepare(args)
        else:
            with action_lock(args.run_id):
                {"run": run_services, "cleanup": cleanup}[args.action](args)
        return 0
    except HarnessError as error:
        print("Harness stopped: " + str(error) + ". No fallback or automatic retry.", file=sys.stderr)
        return 1
    except (OSError, ValueError, ET.ParseError):
        # Exception details may contain a DSN or platform path; do not print them.
        print("Harness stopped safely; inspect the run artifacts. No fallback or automatic retry.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
