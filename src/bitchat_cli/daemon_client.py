from __future__ import annotations

import json
import socket

from .state import RuntimePaths


class DaemonClient:
    def __init__(self, runtime: RuntimePaths) -> None:
        self.runtime = runtime

    def send_command(self, payload: dict[str, object]) -> dict[str, object]:
        if not self.runtime.daemon_socket_path.exists():
            raise RuntimeError("daemon socket not found; start `bitchat daemon start` first")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(str(self.runtime.daemon_socket_path))
            client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            response = b""
            while not response.endswith(b"\n"):
                chunk = client.recv(4096)
                if not chunk:
                    break
                response += chunk
        except OSError as exc:
            raise RuntimeError(f"failed to talk to daemon: {exc}") from exc
        finally:
            client.close()
        if not response:
            raise RuntimeError("daemon returned no response")
        decoded = json.loads(response.decode("utf-8"))
        if not decoded.get("ok", False):
            raise RuntimeError(str(decoded.get("error") or "daemon command failed"))
        return decoded

    def runtime_status(self) -> dict[str, object]:
        response = self.send_command({"command": "runtime_status"})
        runtime_status = response.get("runtime")
        return runtime_status if isinstance(runtime_status, dict) else {}

    def send_dm(self, *, peer_id: str, content: str) -> dict[str, object]:
        return self.send_command(
            {
                "command": "dm_send",
                "peer_id": peer_id,
                "content": content,
            }
        )

    def send_dm_file(self, *, peer_id: str, path: str) -> dict[str, object]:
        return self.send_command(
            {
                "command": "dm_send_file",
                "peer_id": peer_id,
                "path": path,
            }
        )

    def send_public(self, *, content: str) -> dict[str, object]:
        return self.send_command(
            {
                "command": "public_send",
                "content": content,
            }
        )

    def send_public_file(self, *, path: str) -> dict[str, object]:
        return self.send_command(
            {
                "command": "public_send_file",
                "path": path,
            }
        )

    def send_geo(
        self,
        *,
        geohash: str,
        content: str,
        pow_difficulty: int,
        teleported: bool,
    ) -> dict[str, object]:
        return self.send_command(
            {
                "command": "geo_send",
                "geohash": geohash,
                "content": content,
                "pow_difficulty": pow_difficulty,
                "teleported": teleported,
            }
        )

    def set_geo_watches(self, geohashes: list[str]) -> dict[str, object]:
        return self.send_command({"command": "geo_watch_set", "geohashes": geohashes})

    def mark_dm_read(self, *, peer_id: str, message_ids: list[str]) -> dict[str, object]:
        return self.send_command(
            {
                "command": "dm_mark_read",
                "peer_id": peer_id,
                "message_ids": message_ids,
            }
        )
