"""Thin Microsoft Graph client: auth-aware, paginated, CRUD helpers.

Only the endpoints used by the calendaring/contacts/tasks bridge live here;
email (mail folders, send) joins later.
"""
from __future__ import annotations

from typing import Any

import httpx

from excense import ExcenseError
from excense.auth import MsAuth


class GraphError(ExcenseError):
    def __init__(self, method: str, url: str, status: int, body: Any):
        self.method = method
        self.url = url
        self.status = status
        self.detail = body.get("error", {}).get("message", body) if isinstance(body, dict) else body
        super().__init__(f"{method} {url} -> {status}: {self.detail}")

    def __str__(self) -> str:
        return f"{self.method} {self.url} returned {self.status}: {self.detail}"


class GraphClient:
    BASE = "https://graph.microsoft.com"
    TIMEOUT = 60.0

    def __init__(self, auth: MsAuth, api_version: str = "v1.0"):
        self._auth = auth
        self._base = f"{self.BASE}/{api_version}"

    # ------------------------------------------------------------ transport

    def _headers(self, has_body: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._auth.token()}",
            "Accept": "application/json",
        }
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        resp = httpx.request(
            method, url, headers=self._headers("json" in kwargs), timeout=self.TIMEOUT, **kwargs
        )
        if resp.status_code in (401, 403):
            self._auth.force_refresh()
            resp = httpx.request(
                method, url, headers=self._headers("json" in kwargs), timeout=self.TIMEOUT, **kwargs
            )
        return resp

    def _j(self, method: str, path: str, **kwargs) -> dict:
        url = path if path.startswith("http") else self._base + path
        resp = self._request(method, url, **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = {}
            raise GraphError(method, url, resp.status_code, body)
        if resp.status_code == 204:
            return {}
        return resp.json()

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._j("GET", path, params=params)

    def post(self, path: str, payload: dict) -> dict:
        return self._j("POST", path, json=payload)

    def patch(self, path: str, payload: dict) -> dict:
        return self._j("PATCH", path, json=payload)

    def delete(self, path: str) -> None:
        self._j("DELETE", path)

    def list_all(self, path: str, params: dict | None = None) -> list[dict]:
        """Follow @odata.nextLink paging to exhaustion."""
        out: list[dict] = []
        url: str | None = None
        while True:
            if url is None:
                data = self.get(path, params=params or {})
            else:
                data = self._j("GET", url)
            out.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            if not url:
                return out

    # ------------------------------------------------------- identity / hello

    def me(self) -> dict:
        return self.get("/me")