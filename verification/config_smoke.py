from pathlib import Path

from upi_zero_link.server import _configured_proxies, _config_int, load_server_config

config = load_server_config(str(Path(__file__).resolve().parents[1] / "config.example.ini"))
assert config["max_uses"] == "0"
assert _config_int(config, "workers", default=1, minimum=1, maximum=4) == 4
proxies = _configured_proxies(None, config)
assert len(proxies) == 2
assert "india-exit-1.example" in proxies[0]
print("config=max_uses:0 workers:4 proxies:2 sensitive_values=not_printed")
