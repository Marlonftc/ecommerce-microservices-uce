"""Common behaviour of every client: internal key, retries and error groups."""
import uuid

import httpx2
import pytest

from app.clients import inventory_client, product_client
from app.clients.base import ServiceUnavailable, UnexpectedResponse

RESERVED = (200, {"order_id": str(uuid.uuid4()), "status": "RELEASED", "items": []})


def test_internal_calls_send_the_internal_key(fake):
    service = fake(inventory_client, RESERVED)

    inventory_client.release(uuid.uuid4())

    assert service.last.headers["X-Internal-Key"] == "test-internal-key"


def test_public_calls_do_not_send_the_internal_key(fake):
    service = fake(product_client, (200, {"items": [], "total": 0, "limit": 100, "offset": 0}))

    product_client.get_active_products([1])

    assert "X-Internal-Key" not in service.last.headers


def test_connection_error_is_retried_once(fake):
    service = fake(inventory_client, httpx2.ConnectError("refused"), RESERVED)

    inventory_client.release(uuid.uuid4())

    assert len(service.requests) == 2


def test_two_connection_errors_mean_unavailable_and_not_sent(fake):
    service = fake(inventory_client, httpx2.ConnectError("refused"))

    with pytest.raises(ServiceUnavailable) as error:
        inventory_client.release(uuid.uuid4())

    assert len(service.requests) == 2
    assert error.value.request_sent is False


def test_read_timeout_is_not_retried_and_result_is_unknown(fake):
    # The request arrived: repeating it here could duplicate work. The caller decides.
    service = fake(inventory_client, httpx2.ReadTimeout("slow"))

    with pytest.raises(ServiceUnavailable) as error:
        inventory_client.release(uuid.uuid4())

    assert len(service.requests) == 1
    assert error.value.request_sent is True


def test_connect_timeout_is_not_retried(fake):
    service = fake(inventory_client, httpx2.ConnectTimeout("no answer"))

    with pytest.raises(ServiceUnavailable) as error:
        inventory_client.release(uuid.uuid4())

    assert len(service.requests) == 1
    assert error.value.request_sent is False


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
def test_server_errors_are_transient(fake, status):
    fake(inventory_client, (status, {"detail": "error"}))

    with pytest.raises(ServiceUnavailable):
        inventory_client.release(uuid.uuid4())


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_client_errors_are_permanent(fake, status):
    # A wrong internal key or a body the other service rejects: a configuration or code error
    fake(inventory_client, (status, {"detail": "error"}))

    with pytest.raises(UnexpectedResponse) as error:
        inventory_client.release(uuid.uuid4())

    assert error.value.status_code == status


def test_body_that_breaks_the_contract_is_permanent(fake):
    fake(inventory_client, (200, {"unexpected": True}))

    with pytest.raises(UnexpectedResponse):
        inventory_client.release(uuid.uuid4())


def test_conflict_without_code_is_permanent(fake):
    fake(inventory_client, (409, {"detail": "text only"}))

    with pytest.raises(UnexpectedResponse):
        inventory_client.release(uuid.uuid4())
