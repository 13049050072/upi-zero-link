# 提链错误定位与输出

## 线上实测

2026-10-25 在 Oracle 现有 `upi-zero-link:1.1.2` 运行环境，对账号 1285 执行检测模式探针。Node 为 v20.19.2，Sentinel 令牌成功生成，结算接口返回 HTTP 400：

```text
Our systems have detected unusual activity. Please try again later.
```

本次请求未进入创建支付方式、Stripe confirm 或商户 approve。此响应证明请求被上游结算检查拒绝；它未提供具体触发条件，不能据此断言是缺 Node、参数错误、某条代理或账号资格耗尽。

截图另三条 `requires_payment_method/generic_decline` 表示 Stripe 拒绝本次 SetupIntent；截图本身只展示了状态码，没有更细字段，不能单凭截图推断账号促销资格耗尽。后续对仍在库中的 1180 实测取得了 `type/code/decline_code/message`，详见下文。

2026-10-26 对仍在库中的账号 1180 做了一次完整实测，取得了更细的上游快照：`checkout=200`、`stripe_init=200`、税区更新 `200`、`due=0`、支付方式包含 `upi`、`payment_method(type=upi)=200`、`confirm=200`、`approve=approved`，但随后 payment page 返回 SetupIntent：

```text
status=requires_payment_method
last_setup_error.type=card_error
last_setup_error.code=setup_attempt_failed
last_setup_error.decline_code=generic_decline
last_setup_error.message=The latest attempt to set up the payment method has failed.
next_action=absent
```

这说明请求已通过 checkout、金额和 UPI 支付方式闸门，拒绝发生在 Stripe SetupIntent 的 UPI 委托设置阶段；响应没有给出比 `generic_decline` 更具体的触发条件。1180 本次没有 `hosted_instructions_url` 或二维码。该确认请求已经执行过，不能再把同一账号当作未消耗样本重试。

## 修复内容

- `checkout_http_*` 保留稳定错误码，新增 `error_detail`：读取 JSON 的 `error/detail`，只保留 type/code/decline_code/param/message；限制 300 字符，移除已知账号凭据、URL、邮箱和 JWT。
- SetupIntent 拒绝保留 `last_setup_error` 的同一组字段。
- SSE 的 `error/message` 在原错误码后追加详情，turb 现有客户端会展示，无需修改 turb。
- 同步线上已存在但本地尚未提交的 `mode=check`、plan_name、promo_campaign 行为，避免后续发版退回旧接口。
- check 模式在金额和支付方式检测后结束，不创建支付方式或 confirm；成功检测释放预留额度，不计成功提链次数。

## 验证

```bash
/Users/jiangdk/code/personal/turb-gpt-free-register/.venv/bin/python -B -m unittest discover -s tests -q
/Users/jiangdk/code/personal/turb-gpt-free-register/.venv/bin/python -B tests/test_offline.py
git diff --check
```

回归覆盖：checkout 上游原文透传和凭据过滤、非 JSON 响应保留状态码、Stripe 拒绝详情和失效链接不交付、check 模式在 confirm 前结束、检测额度释放、SSE 向现有客户端传出详情。

本轮程序修复不代表上游拒绝已解除，完整成功标准仍是取得核验通过的链接与二维码。

## 状态与确认后的重试

- 根据最新有效的 SetupIntent 快照更新失败标记；`requires_action` 覆盖旧拒绝状态后，不再输出残留的 `declined`。
- 只有失败状态带 `decline_code` 或明确拒绝代码时才归为 `declined`；其他初始化错误归为 `setup_failed`，保留上游详情。
- confirm 提交前记录 `confirm_attempted=True`。提交时网络超时抛出 `ConfirmationUncertain`，CLI 停止整单重建；提交后的失败结果也带此标志。
- instructions 的只读网络请求仍允许在同会话内读取重试；确认前 NetworkDown 的原重试行为保持。
- 当前 SSE 错误仍保留原有兼容格式，此轮没有更换 turb 的错误协议。

## 当前部署

状态与确认后重试修复提交为 `f5c576424b9cbce613c00beeefbeaca56a364568`，Oracle 运行 `upi-zero-link:f5c5764`。候选镜像从 `/` 加载安装包，全量 24 项 unittest 通过；`/app` 和 site-packages 各 24 个运行文件与提交一致。真实 check 请求仍返回 unusual activity，未进入 confirm，检测完成 reserved=0。

验收文件为 `/opt/upi-zero-link/release-f5c5764.json`。回滚：`cd /opt/upi-zero-link && sudo -n cp docker-compose.yml.before-f5c5764 docker-compose.yml && sudo -n docker compose up -d --no-build --force-recreate upi-zero-link`，回到 `upi-zero-link:a2b413e-r2`。

延迟审查提出的 SSE 锁与终态发布、TTL、executor 提交回滚、会话回收和成功响应 JSON 边界，尚未在本轮修改，需逐项复现后处理。

## 2026-10-26 真实拒绝细节补证

- 账号 1285 与 849 的只读 `check_only` 探针均为 Sentinel `t/so` 存在、`checkout HTTP 400`，上游 `detail` 为 `Our systems have detected unusual activity. Please try again later.`；两次都未创建 Stripe PaymentMethod，未提交 confirm。
- 账号 1180 的完整探针走到 `confirm=200`、`approve=approved`，但最终 SetupIntent 为 `requires_payment_method`，`last_setup_error` 为 `card_error / setup_attempt_failed / generic_decline`，消息为 `The latest attempt to set up the payment method has failed.`；`next_action` 为空，因此没有可核验链接或二维码。
- 结论：`unusual activity` 是 OpenAI checkout 建单层拒绝；`generic_decline` 是 Stripe SetupIntent 委托设置层拒绝。两者的细节已被保留并能通过 SSE 传给 turb，但上游仍未给出更具体触发条件，当前仍没有完整成功的链接/二维码交付。

## 首轮部署与入口校验

首轮运行镜像 `upi-zero-link:a2b413e-r2`，代码提交 `a2b413e76dbc3a061c599ebebc708890b76f8126`。`/app` 和 `/usr/local/lib/python3.12/site-packages` 各 24 个运行文件均与本地提交一致，容器 healthy、RestartCount=0，公网 `/healthz` 为正常响应。

控制台入口 `/usr/local/bin/upi-zero-link-server` 从 site-packages 导入包，覆盖 `/app` 后必须重新安装包。候选镜像应从 `/` 运行离线测试，验证实际安装包；只在 `/app` 运行 `python -c` 会优先读当前目录，掩盖安装包未更新。发布定义见 `deploy/Dockerfile.release`：

```bash
sudo -n docker build -f deploy/Dockerfile.release --build-arg VCS_REF=a2b413e76dbc3a061c599ebebc708890b76f8126 -t upi-zero-link:a2b413e-r2 .
```

部署后通过 turb 现有 `_iter_sse_events_with_reconnect` 和 `_extract_error_message` 实测账号 1285，终结错误完整包含 unusual activity，未进入 confirm，链接和二维码均为空，检测结束 reserved=0。此项证明错误透传可用，没有证明上游拒绝已解除。

回滚：在 `/opt/upi-zero-link` 恢复 `docker-compose.yml.before-a2b413e`，执行 `sudo -n docker compose up -d --no-build --force-recreate upi-zero-link`。原镜像 `upi-zero-link:1.1.2` 保留。配置备份为 `server.ini.before-a2b413e`，本轮配置哈希未变。
