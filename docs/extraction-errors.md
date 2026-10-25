# 提链错误定位与输出

## 线上实测

2026-10-25 在 Oracle 现有 `upi-zero-link:1.1.2` 运行环境，对账号 1285 执行检测模式探针。Node 为 v20.19.2，Sentinel 令牌成功生成，结算接口返回 HTTP 400：

```text
Our systems have detected unusual activity. Please try again later.
```

本次请求未进入创建支付方式、Stripe confirm 或商户 approve。此响应证明请求被上游结算检查拒绝；它未提供具体触发条件，不能据此断言是缺 Node、参数错误、某条代理或账号资格耗尽。

截图另三条 `requires_payment_method/generic_decline` 表示 Stripe 拒绝本次 SetupIntent。该代码未提供更细拒绝依据，不能单凭它推断账号促销资格耗尽。

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
