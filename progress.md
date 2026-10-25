## 2026-10-25 - Task: 定位提链失败并保留上游错误原因

### What was done

- 在 Oracle 原容器同环境复现账号 1285 的结算 HTTP 400，确认 Node v20.19.2 与 Sentinel 均正常，上游返回 unusual activity；探针在 confirm 前结束。
- 为结算与 Stripe 委托失败保留脱敏详情，通过 SSE 交付给 turb 现有客户端。
- 同步线上检测模式接口至本地，补齐成功检测后预留额度释放。
- 更正 generic_decline 等于试用资格耗尽的无依据解释，记录现有证据与限制。

### Testing

- 新增错误原文透传、SSE 传出详情、Stripe 拒绝详情测试，修复前出现预期失败，修复后通过。
- 同步检测模式时复现本地误进入 payment_method；同步后检测在第三个 HTTP 响应返回，不发 confirm。
- 检测预留额度测试复现 reserved=1，释放后 reserved=0、used=0。
- 全量 unittest 18 项通过；tests/test_offline.py 自测全部通过；git diff --check 通过。
- 与原 Oracle 运行包对比 24 个文件，仅本轮涉及的 protocol.py / server.py 不同。
- 真实账号 1285 仍返回结算拒绝，尚未取得有效链接／二维码，本轮代码修复不代表上游拒绝已解除。

### Notes

- `upi_zero_link/protocol.py`：提取、限制并过滤上游错误详情；同步原线上检测与套餐参数。
- `upi_zero_link/server.py`：同步原线上检测接口，释放检测预留额度并在错误事件中追加详情。
- `tests/test_protocol_errors.py`：新增 6 项回归，覆盖错误输出与检测模式边界。
- `README.md`：更正错误码解释，去除 generic_decline 等于资格耗尽的断言。
- `docs/extraction-errors.md`：记录实测、协议契约、验证方式与剩余卡点。
- `progress.md`：记录本轮实施与验证。
- 部署回滚点为原镜像 `upi-zero-link:1.1.2`；发版前备份 `/opt/upi-zero-link/docker-compose.yml` 与 `server.ini`，后续追加实际部署验证。回滚将 compose 镜像恢复 `upi-zero-link:1.1.2` 后在 `/opt/upi-zero-link` 执行 `sudo -n docker compose up -d --no-build --force-recreate upi-zero-link`。
