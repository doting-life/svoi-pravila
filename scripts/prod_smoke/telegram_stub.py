"""Offline stand-in for api.telegram.org used only by scripts/prod_smoke.sh.

Serves HTTPS with a test CA certificate, answers every Bot API call with ``ok``,
remembers the URL passed to ``setWebhook`` and every ``sendMessage`` (the alert bot of
Alertmanager talks to it too) so the smoke test can assert them. ``GET /smoke/messages``
returns the recorded messages as JSON. Nothing here ever leaves the compose network.
"""

from __future__ import annotations

import json
import re
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

CERT_FILE = "/stub/tls.crt"
KEY_FILE = "/stub/tls.key"
_METHOD_RE = re.compile(r"^/bot[^/]+/(?P<method>[A-Za-z]+)$")
_BOT = {"id": 123456, "is_bot": True, "first_name": "Smoke", "username": "svoi_smoke_bot"}


def _token(path: str) -> str:
    return path.split("/")[1].removeprefix("bot")


def _webhook_url(body: bytes) -> str:
    values = parse_qs(body.decode(), keep_blank_values=True).get("url", [""])
    return values[0]


_MESSAGES: list[dict[str, object]] = []


class _State:
    webhook_url = ""


class _Handler(BaseHTTPRequestHandler):
    def _reply(self, result: object) -> None:
        payload = json.dumps({"ok": True, "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path != "/smoke/messages":
            self.send_error(404)
            return
        payload = json.dumps(_MESSAGES).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        match = _METHOD_RE.match(self.path)
        method = match.group("method") if match else ""
        if method == "getMe":
            self._reply(_BOT)
        elif method == "setWebhook":
            _State.webhook_url = _webhook_url(body)
            self._reply(result=True)
        elif method == "getWebhookInfo":
            self._reply({"url": _State.webhook_url, "pending_update_count": 0})
        elif method == "sendMessage":
            sent = json.loads(body)
            _MESSAGES.append({"token": _token(self.path), **sent})
            # Like the real API, return the sent message with its chat (the notifier reads it).
            chat = {"id": int(sent["chat_id"]), "type": "supergroup"}
            self._reply({"message_id": len(_MESSAGES), "date": 0, "chat": chat, "text": sent["text"]})
        else:
            self._reply(result=True)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def main() -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERT_FILE, KEY_FILE)
    server = ThreadingHTTPServer(("0.0.0.0", 443), _Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
