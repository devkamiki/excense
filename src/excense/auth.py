"""Microsoft Entra authentication: device-code flow with a persistent token cache.

The gateway runs headless (Docker VPS), so the interactive part is the
*device code* flow: the CLI prints a code + URL, you approve on any device,
and refresh tokens are then persisted and reused automatically by the sync
loop. Tokens are tied to the app registration you create in Entra.
"""
from __future__ import annotations

import json
import os
from urllib.parse import parse_qs, urlparse

from msal import PublicClientApplication, SerializableTokenCache

from excense import ExcenseError
from excense.config import Settings


class AuthError(ExcenseError):
    pass


class MsAuth:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache = SerializableTokenCache()
        self.token_path = settings.token_path
        if self.token_path.exists():
            self._cache.deserialize(self.token_path.read_text())
        self._app = PublicClientApplication(
            settings.client_id,
            authority=settings.authority,
            token_cache=self._cache,
        )

    # ------------------------------------------------------------------ cache

    def _save(self) -> None:
        if self._cache.has_state_changed:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(self._cache.serialize())
            os.chmod(self.token_path, 0o600)

    # ------------------------------------------------------------- acquisition

    def has_account(self) -> bool:
        return bool(self._app.get_accounts())

    def device_code(self) -> dict:
        if not self.settings.client_id:
            raise AuthError(
                "EXCENSE_CLIENT_ID is not set. Register a public mobile/desktop "
                "app in Entra (see README) and set it first."
            )
        flow = self._app.initiate_device_flow(
            scopes=self.settings.graph_scopes
        )
        if "user_code" not in flow:
            raise AuthError(flow.get("error_description") or "Device flow failed")
        print(flow["message"])
        result = self._app.acquire_token_by_device_flow(flow)
        self._save()
        if "access_token" in result:
            return result
        raise AuthError(result.get("error_description") or "Device flow failed")

    def auth_code_url(self) -> dict:
        """Start a browser authorization-code flow with paste-back sign-in.

        Conditional Access policies often ban the device-code flow (error
        53003) while still allowing ordinary interactive browser sign-ins,
        so `auth-web` is the fallback when `auth` is refused.
        """
        if not self.settings.client_id:
            raise AuthError(
                "EXCENSE_CLIENT_ID is not set. Register a public mobile/desktop "
                "app in Entra (see README) and set it first."
            )
        return self._app.initiate_auth_code_flow(
            self.settings.graph_scopes,
            redirect_uri=self.settings.redirect_uri,
            # Paste-back needs the code in the URL bar (query); form_post
            # would bury it in a POST body no listener receives. PKCE binds
            # the code to this verifier, so a copied-around code is inert.
            response_mode="query",
        )

    def complete_auth_code(self, flow: dict, pasted_url: str) -> None:
        """Finish the auth-code flow with the redirect URL pasted back."""
        params = {
            key: values[0]
            for key, values in parse_qs(urlparse(pasted_url).query).items()
        }
        result = self._app.acquire_token_by_auth_code_flow(flow, params)
        self._save()
        if "access_token" not in result:
            raise AuthError(
                result.get("error_description") or "authorization-code flow failed"
            )

    def _silent(self, *, force: bool = False) -> dict | None:
        accounts = self._app.get_accounts()
        if not accounts:
            return None
        result = self._app.acquire_token_silent(
            self.settings.graph_scopes, account=accounts[0], force_refresh=force
        )
        self._save()
        return result

    def token(self) -> str:
        """Access token, running the device flow on first use."""
        result = self._silent()
        if not result or "access_token" not in result:
            result = self.device_code()
        if "access_token" not in result:
            raise AuthError(result.get("error_description") or "Could not acquire token")
        return result["access_token"]

    def force_refresh(self) -> str:
        """Force a token refresh (used to ride out 401s)."""
        result = self._silent(force=True)
        if not result or "access_token" not in result:
            # No cached session yet -> interactive. Do not silently block the
            # sync loop; surface the error for the operator to run `excense auth`.
            raise AuthError(
                "No cached session to refresh. Run `excense auth` interactively once."
            )
        return result["access_token"]