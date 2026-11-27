"""SSRF guard, redirect pinning and size cap for the discovery poster proxy."""
import tempfile
import unittest
from unittest.mock import patch

import app as tbh


def _resolving_to(*ips, family=2):
    def fake(host, port, *args, **kwargs):
        return [(family, 1, 6, "", (ip, port)) for ip in ips]
    return patch("socket.getaddrinfo", side_effect=fake)


class _FakeResponse:
    def __init__(self, status_code=200, body=b"", headers=None, chunks=None):
        self.status_code = status_code
        self.content = body
        self.headers = headers or {}
        self._chunks = chunks
        self._body = body
        self.closed = False

    def iter_content(self, chunk_size=8192):
        if self._chunks is not None:
            yield from self._chunks
        elif self._body:
            yield self._body

    def close(self):
        self.closed = True


class _FakeSession:
    """Stands in for the one-shot requests.Session used by the proxy."""

    def __init__(self):
        self.response = None
        self.error = None
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        if self.response is None:
            raise AssertionError("no response configured")
        return self.response

    def close(self):
        self.closed = True


class PublicUrlGuardTests(unittest.TestCase):
    """The proxy delegates SSRF filtering to metadata_engine._poster_url_is_public."""

    @staticmethod
    def _allowed(url):
        return tbh.meta_eng._poster_url_is_public(url)

    def test_accepts_public_address(self):
        with _resolving_to("93.184.216.34"):
            allowed, reason = self._allowed("http://cdn.example.com/a.jpg")
        self.assertTrue(allowed, reason)

    def test_rejects_private_and_special_addresses(self):
        for address in ("127.0.0.1", "127.0.0.2", "10.0.0.5", "192.168.1.1", "172.16.4.4",
                        "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1",
                        "fe80::1", "fd00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1"):
            with self.subTest(address=address):
                with _resolving_to(address):
                    allowed, _reason = self._allowed("http://cdn.example.com/a.jpg")
                self.assertFalse(allowed)

    def test_rejects_when_any_resolved_address_is_private(self):
        with _resolving_to("93.184.216.34", "10.1.2.3"):
            allowed, _reason = self._allowed("http://cdn.example.com/a.jpg")
        self.assertFalse(allowed)

    def test_rejects_local_hostnames_without_resolution(self):
        for url in ("http://localhost/a.jpg", "http://nas.local/a.jpg",
                    "http://localhost.localdomain/a.jpg"):
            with self.subTest(url=url), patch("socket.getaddrinfo") as gai:
                allowed, _reason = self._allowed(url)
                self.assertFalse(allowed)
                gai.assert_not_called()

    def test_rejects_non_http_scheme_and_credentials(self):
        for url in ("file:///etc/passwd", "ftp://cdn.example.com/a.jpg",
                    "http://user:pass@cdn.example.com/a.jpg", "http:///nohost"):
            with self.subTest(url=url):
                allowed, _reason = self._allowed(url)
                self.assertFalse(allowed)

    def test_rejects_unresolvable_host(self):
        with patch("socket.getaddrinfo", side_effect=OSError("no such host")):
            allowed, _reason = self._allowed("http://does-not-exist.example/a.jpg")
        self.assertFalse(allowed)


class PosterHostAllowlistTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict("os.environ", {"TBH_POSTER_HOST_ALLOWLIST": ""}):
            self.assertTrue(tbh._poster_host_allowed("anything.example"))

    def test_suffix_and_exact_match(self):
        with patch.dict("os.environ", {"TBH_POSTER_HOST_ALLOWLIST": " missav.ws , *.fourhoi.com,"}):
            self.assertTrue(tbh._poster_host_allowed("missav.ws"))
            self.assertTrue(tbh._poster_host_allowed("cdn.missav.ws"))
            self.assertTrue(tbh._poster_host_allowed("img.fourhoi.com"))
            self.assertFalse(tbh._poster_host_allowed("evil.example"))
            self.assertFalse(tbh._poster_host_allowed("notmissav.ws"))


class PosterProxyRouteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache_patch = patch.object(tbh.db, "CACHE_DIR", tmp.name)
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        self.session = _FakeSession()
        session_patch = patch.object(tbh.requests, "Session", return_value=self.session)
        session_patch.start()
        self.addCleanup(session_patch.stop)
        self.client = tbh.app.test_client()

    def _get(self, url):
        return self.client.get(f"/api/discover/poster-proxy?url={url}")

    def test_loopback_is_blocked_without_any_outbound_request(self):
        with _resolving_to("127.0.0.1"):
            res = self._get("http://evil.example/a.jpg")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.session.calls, [])

    def test_allowlist_blocks_host_even_when_public(self):
        with _resolving_to("93.184.216.34"), \
                patch.dict("os.environ", {"TBH_POSTER_HOST_ALLOWLIST": "missav.ws"}):
            res = self._get("http://cdn.example.com/a.jpg")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.session.calls, [])

    def test_public_image_is_cached_disabled_redirects_and_served(self):
        body = b"\xff\xd8\xff\xe0" + b"x" * 600
        self.session.response = _FakeResponse(
            200, body, {"Content-Type": "image/jpeg", "Content-Length": str(len(body))})
        with _resolving_to("93.184.216.34"):
            res = self._get("http://cdn.example.com/a.jpg")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "image/jpeg")
        self.assertTrue(self.session.response.closed)
        self.assertTrue(self.session.closed)
        _url, kwargs = self.session.calls[0]
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(kwargs["stream"])

    def test_redirect_to_metadata_endpoint_is_blocked(self):
        self.session.response = _FakeResponse(302, b"", {"Location": "http://169.254.169.254/latest/meta-data/"})

        def resolve(host, port, *args, **kwargs):
            ip = "169.254.169.254" if host == "169.254.169.254" else "93.184.216.34"
            return [(2, 1, 6, "", (ip, port))]

        with patch("socket.getaddrinfo", side_effect=resolve):
            res = self._get("http://cdn.example.com/a.jpg")
        self.assertEqual(res.status_code, 403)
        self.assertTrue(self.session.response.closed)
        self.assertEqual(len(self.session.calls), 1)
        self.assertTrue(self.session.closed)

    def test_oversized_content_length_is_rejected(self):
        self.session.response = _FakeResponse(200, b"x" * 600, {
            "Content-Type": "image/jpeg",
            "Content-Length": str(tbh._PROXY_IMAGE_MAX_BYTES + 1),
        })
        with _resolving_to("93.184.216.34"):
            res = self._get("http://cdn.example.com/a.jpg")
        self.assertEqual(res.status_code, 413)
        self.assertTrue(self.session.response.closed)

    def test_oversized_stream_is_rejected_without_content_length(self):
        chunk = b"x" * (64 * 1024)
        self.session.response = _FakeResponse(
            200, b"", {"Content-Type": "image/jpeg"},
            chunks=[chunk] * (tbh._PROXY_IMAGE_MAX_BYTES // len(chunk) + 2))
        with _resolving_to("93.184.216.34"):
            res = self._get("http://cdn.example.com/a.jpg")
        self.assertEqual(res.status_code, 413)
        self.assertTrue(self.session.response.closed)

    def test_non_image_content_type_returns_415_not_502(self):
        self.session.response = _FakeResponse(
            200, b"<html>" + b"x" * 400, {"Content-Type": "text/html; charset=utf-8"})
        with _resolving_to("93.184.216.34"):
            res = self._get("http://cdn.example.com/a.jpg")
        self.assertEqual(res.status_code, 415)
        self.assertTrue(self.session.response.closed)

    def test_timeout_maps_to_502(self):
        from requests.exceptions import Timeout

        self.session.error = Timeout()
        with _resolving_to("93.184.216.34"):
            res = self._get("http://cdn.example.com/a.jpg")
        self.assertEqual(res.status_code, 502)
        self.assertTrue(self.session.closed)


if __name__ == "__main__":
    unittest.main()