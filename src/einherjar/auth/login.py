"""The login flow: listen on loopback, open the browser, exchange the code.

The listener is a real socket, because the browser needs somewhere to return to. It is started
before the browser is opened, so a fast callback cannot race a listener that does not exist yet.
"""

from __future__ import annotations

import asyncio
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from queue import Empty, Queue
from urllib.parse import parse_qs, urlparse

import httpx

from einherjar.auth.pkce import authorize_url, code_challenge, code_verifier, exchange_code
from einherjar.auth.tokens import Tokens, TokenStore

_PAGE = (
    "<html><body style='font-family: sans-serif; padding: 2rem'>"
    "<h2>Einherjar</h2><p>{message}</p><p>You can close this tab.</p></body></html>"
)


class LoginError(RuntimeError):
    """The flow did not produce a usable token."""


class LoginFlow:
    """One pending authorization: a verifier, a state, and a socket waiting for the callback."""

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        resource: str,
        redirect_uri: str | None = None,
    ) -> None:
        self.issuer = issuer
        self.client_id = client_id
        self.resource = resource
        self._requested_redirect = redirect_uri
        self._verifier = code_verifier()
        self.state = secrets.token_urlsafe(16)
        self._events: Queue[tuple[str | None, str | None, str | None]] = Queue()
        self._server: HTTPServer | None = None
        self.redirect_uri: str | None = None
        self.url: str | None = None

    def start(self) -> str:
        """Bind the loopback listener and build the authorization URL."""
        if self._requested_redirect:
            parsed = urlparse(self._requested_redirect)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 0
        else:
            host, port = "127.0.0.1", 0

        events = self._events

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the name is http.server's contract
                query = parse_qs(urlparse(self.path).query)
                code = query.get("code", [None])[0]
                state = query.get("state", [None])[0]
                error = query.get("error", [None])[0]
                events.put((code, state, error))
                body = _PAGE.format(
                    message="Sign-in received." if code else f"Sign-in failed: {error or 'no code'}"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                pass

        try:
            self._server = HTTPServer((host, port), Handler)
        except OSError as exc:
            raise LoginError(
                f"cannot bind {host}:{port} for the sign-in callback ({exc}). "
                "Close whatever holds the port, or register a different redirect URI."
            ) from exc
        bound_port = self._server.server_port
        self.redirect_uri = f"http://{host}:{bound_port}/callback"
        self.url = authorize_url(
            issuer=self.issuer,
            client_id=self.client_id,
            redirect_uri=self.redirect_uri,
            state=self.state,
            challenge=code_challenge(self._verifier),
            resource=self.resource,
        )
        return self.url

    def wait(self, *, timeout: float = 300) -> str:
        """Serve one callback and return its code. The state is checked here, not later."""
        if self._server is None:
            raise LoginError("the flow has not been started")
        self._server.timeout = timeout
        self._server.handle_request()
        try:
            code, state, error = self._events.get_nowait()
        except Empty as exc:
            raise LoginError("no callback arrived before the timeout") from exc
        if error:
            raise LoginError(f"the issuer refused the sign-in: {error}")
        if not code:
            raise LoginError("the callback carried no authorization code")
        if state != self.state:
            raise LoginError("the callback state did not match this sign-in")
        return code

    async def finish(self, code: str, *, client: httpx.AsyncClient) -> Tokens:
        if self.redirect_uri is None:
            raise LoginError("the flow has not been started")
        return await exchange_code(
            client,
            issuer=self.issuer,
            client_id=self.client_id,
            code=code,
            verifier=self._verifier,
            redirect_uri=self.redirect_uri,
            resource=self.resource,
        )

    def close(self) -> None:
        if self._server is not None:
            self._server.server_close()
            self._server = None


async def login(
    *,
    issuer: str,
    client_id: str,
    resource: str,
    store: TokenStore,
    redirect_uri: str | None = None,
    open_browser=webbrowser.open,
    client: httpx.AsyncClient | None = None,
    timeout: float = 300,
) -> Tokens:
    """Complete one sign-in and store the result. One browser step per machine."""
    flow = LoginFlow(
        issuer=issuer,
        client_id=client_id,
        resource=resource,
        redirect_uri=redirect_uri,
    )
    url = flow.start()
    try:
        open_browser(url)
        code = await asyncio.to_thread(flow.wait, timeout=timeout)
        owned = client is None
        session = client or httpx.AsyncClient()
        try:
            tokens = await flow.finish(code, client=session)
        finally:
            if owned:
                await session.aclose()
    finally:
        flow.close()

    store.save(tokens)
    return tokens
