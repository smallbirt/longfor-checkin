# 签到提交失败诊断

旧版把部分提交错误合并为“签到请求异常，复核仍未签到”，没有保存具体原因，已经产生的历史记录无法补回。

本版保留安全的 HTTP 状态码、数字业务码、网络错误分类和失败阶段。运行记录与邮件会显示安全原因；运行记录结果另有 `diagnostics` 数组。容器日志包含 `longfor_api_error` 标记。

只输出白名单元数据，不输出账号 Token、Cookie、手机号、设备指纹、验证码证明、原始请求／响应、URL 或异常文本。诊断不改变签到提交和复核次数，不开启计划，不改变用户凭证。

## 服务器更新

将 `longfor-checkin-diagnostics-release.zip` 上传到：

```text
/opt/longfor-checkin/longfor-checkin-diagnostics-release.zip
```

在服务器完整执行：

```bash
(
set -e
umask 077
release_zip=/opt/longfor-checkin/longfor-checkin-diagnostics-release.zip
test -f "$release_zip"
python3 -m zipfile -t "$release_zip"
release_dir="$(mktemp -d /opt/longfor-checkin/longfor-checkin-diagnostics.XXXXXX)"
python3 -m zipfile -e "$release_zip" "$release_dir"
python3 "$release_dir/longfor-checkin/scripts/connect_pg.py" \
  --app-dir /opt/longfor-checkin/longfor-checkin \
  --project longfor-checkin
)
```

当前服务器已接入 PG，脚本备份现有 PG、密钥和代码后更新，跳过旧 SQLite 迁移。保留现有管理员密码、用户配置、设备配置、端口与计划。只切换签到容器，失败时尝试回退。未修改 HR 表或重启 HR。

成功后检查：

```bash
cd /opt/longfor-checkin/longfor-checkin
curl --noproxy '*' --max-time 10 http://127.0.0.1:8091/health
docker compose -p longfor-checkin logs --timestamps --since 10m --tail=100 checkin
```

升级本身不主动发短信或提交诊断签到；原本启用的计划按原规则继续。不要重新运行 setup、修改 DATABASE_URL 或删除数据卷。

## 下次问题如何查看

先在网页查询小程序状态。如果已签到，不再提交。如果仍未签到，可由本人主动点一次小程序“尝试签到”，或等原有计划下一次运行；本轮不会因诊断而额外重试提交。

查看：

```bash
docker compose -p longfor-checkin logs --timestamps --since 10m --tail=100 checkin
```

找到 `longfor_api_error` 行，或从网页运行记录／邮件查看具体原因。`operation=submit` 表示签到提交，`query` 表示状态查询。`reason=http` 为 HTTP 错误；`business` 为服务端业务码；`timeout`、`tls`、`connection`、`network` 分别为超时、TLS、连接和其他网络错误；`invalid_response` 表示响应格式或字段不符合预期。

运行记录中的 `diagnostics.stage` 可区分初次查询 `initial_status`、提交 `submit`、滑块后查询 `status_after_captcha`、携带验证结果提交 `submit_after_captcha`、最终复核 `verify_status`。

单条请求错误日志不等于整轮失败。例如提交超时后查询已签到，最终仍报告成功；已知滑块挑战也可能先产生一条请求日志。最终以运行记录的 `state` 与服务端复核结果为准。

## 历史记录的限制

旧版小程序“提交异常，复核仍未签到”记录缺少原始错误码，无法判定是 HTTP／业务拒绝、网络、设备共享风险或其他原因。`captcha_attempted=false` 只表示本轮没有进入程序的滑块处理流程，不能排除其他风控。

本版只增强诊断，不宣称已修复未知的小程序提交失败，也没有自动提交真实签到进行验证。需从之后的请求获取新错误码。
