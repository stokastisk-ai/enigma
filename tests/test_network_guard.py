"""The session network guard (tests/conftest.py): tests cannot reach the internet or any AWS endpoint; loopback and unix sockets still work."""
from __future__ import annotations

import os
import socket
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("ENIGMA_ALLOW_NETWORK") == "1", reason="guard deliberately off")


@pytest.mark.parametrize("host", ["example.com", "cognito-idp.us-east-1.amazonaws.com", "dynamodb.us-east-1.amazonaws.com", "8.8.8.8"])
def test_external_hosts_are_unreachable_from_tests(host):
    with pytest.raises(OSError, match="non-loopback|external host"):
        socket.create_connection((host, 443), timeout=1)


def test_https_requests_to_aws_endpoints_are_refused():
    with pytest.raises(OSError):
        urllib.request.urlopen("https://cognito-idp.us-east-1.amazonaws.com/x/.well-known/jwks.json", timeout=1)   # noqa: S310


def test_loopback_still_works():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    c = socket.create_connection(srv.getsockname(), timeout=2)
    c.close(); srv.close()
    with pytest.raises(ConnectionRefusedError):                       # a closed local port fails normally: it was ALLOWED to try
        socket.create_connection(("127.0.0.1", 1), timeout=1)
