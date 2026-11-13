"""Compare construction arguments without opening a socket."""
from bitchat_cli.network import NetworkConfig

config = NetworkConfig(proxy_mode="disabled", proxy_url=None, proxy_policy="require")
try:
    config.make_http_client(user_agent="local-fixture")
except RuntimeError as error:
    assert "proxy policy is require" in str(error)
else:
    raise AssertionError("HTTP accepted a disabled required proxy")
assert config.websocket_proxy_argument() == {"proxy": None}

custom = NetworkConfig(
    proxy_mode="custom", proxy_url="https://example:fixture@proxy.invalid:1234", proxy_policy="prefer",
)
assert custom.redacted_proxy_target() == "https://proxy.invalid:1234"
print("HTTP rejected disabled/require; websocket construction selected a direct connection.")
