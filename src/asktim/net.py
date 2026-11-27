"""A small HTTP client on the standard library: JSON requests and server-sent events (SSE).

Proxies are taken from the environment (HTTPS_PROXY ...), as urllib does. Certificates come
from the system; if Python has none (a common problem with python.org installers on macOS)
and the certifi package is installed, its certificates are used.
"""

import json
import socket
import ssl
import time
import urllib.error
import urllib.request

from . import __version__

USER_AGENT = "AskTim/%s (+https://github.com/comparativechrono/AskTim)" % __version__

_ctx = None


def _ssl_context():
    global _ctx
    if _ctx is None:
        ctx = ssl.create_default_context()
        try:
            if not ctx.get_ca_certs():
                import certifi  # type: ignore

                ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            pass
        _ctx = ctx
    return _ctx


class HTTPError(Exception):
    """An answer from the service that is not a success, or no answer at all."""

    def __init__(self, message, status=None, body=None, retry_after=None, network=False):
        Exception.__init__(self, message)
        self.status = status
        self.body = body
        self.retry_after = retry_after
        self.network = network

    @property
    def retryable(self):
        return self.network or self.status in (408, 409, 425, 429, 500, 502, 503, 504, 529)


def _retry_after(headers):
    try:
        v = headers.get("retry-after")
        if v is None:
            return None
        return max(0.0, float(v))
    except Exception:
        return None


def open_request(url, headers, body, timeout=90.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"content-type": "application/json", "user-agent": USER_AGENT}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())
    except urllib.error.HTTPError as e:
        raw = b""
        try:
            raw = e.read()
        except Exception:
            pass
        text = raw.decode("utf-8", "replace")
        raise HTTPError("HTTP %s" % e.code, status=e.code, body=text, retry_after=_retry_after(e.headers or {}))
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(reason).upper():
            raise HTTPError(
                "Could not check the service's certificate (%s). On a Mac with Python from python.org, run "
                "'Install Certificates.command' in the Python folder, or `pip install certifi`." % reason,
                network=False,
            )
        raise HTTPError("Could not reach %s (%s)." % (url.split("/")[2], reason), network=True)
    except (socket.timeout, TimeoutError):
        raise HTTPError("No answer from %s within %d seconds." % (url.split("/")[2], timeout), network=True)
    except (ConnectionError, OSError) as e:
        raise HTTPError("The connection to %s failed (%s)." % (url.split("/")[2], e), network=True)


def post_json(url, headers, body, timeout=90.0):
    resp = open_request(url, headers, body, timeout)
    try:
        raw = resp.read()
    finally:
        resp.close()
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise HTTPError("The service sent something that is not JSON.", status=getattr(resp, "status", None), body=raw[:2000])


def get_json(url, headers, timeout=30.0):
    """GET a JSON document (a list of models, say)."""
    return post_json(url, headers, None, timeout)


def iter_sse(resp):
    """(event, data) pairs from a server-sent event stream. data is the joined data: lines."""
    event, data = None, []
    try:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                if data:
                    yield event, "\n".join(data)
                event, data = None, []
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)
        if data:
            yield event, "\n".join(data)
    except (socket.timeout, TimeoutError):
        raise HTTPError("The answer stopped arriving (no data for a while).", network=True)
    except (ConnectionError, OSError) as e:
        raise HTTPError("The connection broke during the answer (%s)." % e, network=True)
    finally:
        try:
            resp.close()
        except Exception:
            pass


def with_retries(fn, attempts=3, on_wait=None, max_wait=60.0):
    """Call fn(); when the service is busy or unreachable, wait and try again (up to `attempts` times)."""
    delay = 4.0
    for i in range(attempts + 1):
        try:
            return fn()
        except HTTPError as e:
            if not e.retryable or i >= attempts:
                raise
            wait = e.retry_after if e.retry_after is not None else delay
            wait = min(max_wait, max(1.0, wait))
            if on_wait:
                on_wait(e, wait)
            time.sleep(wait)
            delay *= 2
