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

## 2026-10-25 - Task: 提链错误详情修复 Oracle 发版验收

### What was done

- 代码提交 a2b413e 已推送并核对远端 master；Oracle 最终运行 `upi-zero-link:a2b413e-r2`。
- 纠正首个覆盖镜像未更新 site-packages 的部署问题，重新安装包后验证入口实际导入位置。
- 保存可复用发布 Dockerfile 与部署、回滚说明。

### Testing

- Oracle Python 3.12 候选镜像从 `/`（读取 site-packages）运行全量 unittest 18 项通过；离线核验自测全部通过。
- `/app` 与 site-packages 各 24 个运行文件逐一 SHA-256 对照提交一致，differences 为空。
- 线上健康状态 healthy、RestartCount=0，公网 `/healthz` ok=true；配置哈希一致，重建前没有进行中的 turb 提链账号。
- 通过 turb 原客户端创建一次 mode=check，账号1285仍获 HTTP400，但 SSE 错误完整返回 `checkout_http_400: Our systems have detected unusual activity. Please try again later.`；没有 confirm、没有交付链接/二维码，结束后 reserved=0。
- 保留两次失败验收：首镜像仅覆盖 /app 导致错误仍旧；第一次重新打包因无 setuptools 且关闭 build isolation 构建失败，恢复 build isolation 后构建通过。均未作为成功结果汇报。

### Notes

- `deploy/Dockerfile.release`：覆盖源码后安装包，确保控制台入口运行新代码。
- `docs/extraction-errors.md`：追加最终镜像、真实探针和入口加载校验。
- `progress.md`：追加部署验收与失败尝试记录。
- 验收记录 `/opt/upi-zero-link/release-a2b413e.json`；源码快照 `/home/ubuntu/upi-zero-link-build-a2b413e`。
- 回滚：`cd /opt/upi-zero-link && sudo -n cp docker-compose.yml.before-a2b413e docker-compose.yml && sudo -n docker compose up -d --no-build --force-recreate upi-zero-link`。原镜像1.1.2保留；`server.ini.before-a2b413e`为配置备份。
- 上游 unusual activity 与截图中 generic_decline 的拒绝未解除；本轮修复错误详情和额度占用，未声称出码成功。
