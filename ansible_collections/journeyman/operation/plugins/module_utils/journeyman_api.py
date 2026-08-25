import json
import os
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class JourneymanApiError(RuntimeError):
    pass


class JourneymanApiClient:
    def __init__(self, url=None, token=None, validate_certs=True, timeout=30):
        self.url = str(url or os.environ.get("JOURNEYMAN_URL") or "").rstrip("/")
        self.token = str(token or os.environ.get("JOURNEYMAN_API_TOKEN") or "").strip()
        self.validate_certs = bool(validate_certs)
        self.timeout = int(timeout)
        if not self.url or not self.token:
            raise JourneymanApiError("Journeyman URL and API token are required.")

    def request(self, method, path, payload=None, query=None):
        url = self.url + path
        if query:
            url += "?" + urlencode(query)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = Request(url, data=body, method=method, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        context = None if self.validate_certs else ssl._create_unverified_context()
        try:
            with urlopen(req, timeout=self.timeout, context=context) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                document = json.loads(exc.read().decode("utf-8"))
                message = document.get("error", {}).get("message") or str(exc)
            except Exception:
                message = str(exc)
            raise JourneymanApiError(message) from exc
        except URLError as exc:
            raise JourneymanApiError(str(exc.reason)) from exc

    def project_by_name(self, name):
        data = self.request("GET", "/api/v1/projects", query={"name": name})
        rows = data.get("projects", [])
        if len(rows) != 1:
            raise JourneymanApiError('Project "{}" was not found.'.format(name))
        return rows[0]

    def package_by_name(self, name):
        data = self.request("GET", "/api/v1/packages", query={"name": name})
        rows = data.get("packages", [])
        if len(rows) != 1:
            raise JourneymanApiError('Package "{}" was not found or is not available.'.format(name))
        return rows[0]
