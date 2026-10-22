"""Shared HTTP logic for the calls to the other services.

Every failure ends in one of three groups, so order-service can decide
what to do without looking at HTTP details:

  ServiceUnavailable  transient: timeout, connection error, 408, 429, 5xx. Retry later.
  Conflict            business answer (409) with a code. Decide by the code.
  UnexpectedResponse  permanent: 400/422 (contract) or 401/403 (internal key or
                      configuration) or a body that breaks the contract.
                      Retrying will not help; it needs to be reviewed.
"""
import logging
from typing import Any

import httpx2

from app.config import settings

logger = logging.getLogger(__name__)


class ServiceUnavailable(Exception):
    """The service did not give a usable answer. The operation can be retried.

    request_sent tells whether the request may have reached the service:
    after a read timeout the result is unknown (the payment may exist).
    """

    def __init__(self, service: str, reason: str, request_sent: bool):
        super().__init__(f"{service} unavailable: {reason}")
        self.service = service
        self.reason = reason
        self.request_sent = request_sent


class Conflict(Exception):
    """A 409 answer. code comes from the body of the other service."""

    def __init__(self, service: str, code: str, detail: dict[str, Any]):
        super().__init__(f"{service} conflict: {code}")
        self.service = service
        self.code = code
        self.detail = detail


class UnexpectedResponse(Exception):
    """An answer that should never happen if both sides follow the contract."""

    def __init__(self, service: str, status_code: int, reason: str = ""):
        super().__init__(f"{service} answered {status_code} {reason}".strip())
        self.service = service
        self.status_code = status_code


class ServiceClient:
    def __init__(self, name: str, base_url: str, internal: bool = True,
                 transport: httpx2.BaseTransport | None = None):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.internal = internal
        self.transport = transport  # only replaced in tests

    def request(self, method: str, path: str, *, json: Any = None,
                params: dict[str, Any] | None = None,
                headers: dict[str, str] | None = None) -> httpx2.Response:
        """Sends the request and returns any 2xx or 4xx response.

        Retries once only when the connection could not be opened: then the
        request never left this service, so repeating it is always safe.
        """
        all_headers = dict(headers or {})
        if self.internal:
            all_headers["X-Internal-Key"] = settings.internal_api_key
        timeout = httpx2.Timeout(settings.http_read_timeout_seconds,
                                 connect=settings.http_connect_timeout_seconds)

        for attempt in (1, 2):
            try:
                with httpx2.Client(base_url=self.base_url, timeout=timeout,
                                   transport=self.transport) as client:
                    response = client.request(method, path, json=json, params=params,
                                              headers=all_headers)
                break
            except httpx2.ConnectError as exc:
                if attempt == 2:
                    raise ServiceUnavailable(self.name, f"connection failed: {exc}", request_sent=False)
                logger.info("%s: connection failed, retrying once", self.name)
            except httpx2.ConnectTimeout as exc:
                raise ServiceUnavailable(self.name, f"connect timeout: {exc}", request_sent=False)
            except httpx2.TimeoutException as exc:
                # The request was sent: the other service may have done the work
                raise ServiceUnavailable(self.name, f"timeout: {exc}", request_sent=True)
            except httpx2.TransportError as exc:
                raise ServiceUnavailable(self.name, f"transport error: {exc}", request_sent=True)

        # 5xx, 408 (request timeout) and 429 (too many requests): try again later
        if response.status_code >= 500 or response.status_code in (408, 429):
            raise ServiceUnavailable(self.name, f"status {response.status_code}", request_sent=True)
        return response


def json_body(service: str, response: httpx2.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        raise UnexpectedResponse(service, response.status_code, "body is not JSON")


def conflict_from(service: str, response: httpx2.Response) -> Conflict:
    """Builds a Conflict from a 409 answer: {"detail": {"code": ..., ...}}."""
    detail = json_body(service, response).get("detail")
    if not isinstance(detail, dict) or "code" not in detail:
        raise UnexpectedResponse(service, 409, "conflict without a code")
    return Conflict(service, detail["code"], detail)
