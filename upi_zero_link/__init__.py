"""upi-zero-link · 印度 ₹0 UPI Autopay 提链（协议层，无浏览器）。

模块：
  identity   设备指纹 / 通用头
  addresses  印度真实地址池
  verify     交付前核验（intent_state + fam）
  protocol   8 步提链流程
  cli        命令行入口
  server     兼容 turb-gpt-free-register 的 JSON + SSE 服务端
"""

__version__ = "1.1.0"

__all__ = ["extract", "__version__"]


def __getattr__(name: str):
    """Keep the public ``extract`` export lazy for the HTTP server and health checks."""
    if name == "extract":
        from .protocol import extract

        return extract
    raise AttributeError(name)
