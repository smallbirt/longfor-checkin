# 两端短信更新版部署与使用

> 历史版本说明：当前本地已改为登录分离，App 短信只更新 App，小程序单独导入微信原生登录／签到页记录。下文旧包与“两端短信”操作不适用于登录分离版。本次只更新本地服务，尚未更新云服务器。

本文保留稳定短信版旧包部署说明。最新平台默认设备版见 [SHARED-DEVICE.md](SHARED-DEVICE.md)，可使用新包中的同一 PG 接入脚本升级。首次设备信息可由管理员提供，普通用户无需逐个导入。

本版网页可选择更新 App、小程序或两端凭证：通过龙湖 App 短信登录，再分别向所选签到活动查询验证后保存。首次仍需本人的 App 登录设备配置，以及各端签到设备信息。已有账号、邮件、每日计划与 PG 支持保留。

## 上传（本地 Mac）

```bash
scp /Users/wpy/Documents/ChatGPT/longfor-checkin-sms-both-release.zip root@120.55.246.247:/opt/longfor-checkin/
```

也可用文件管理器上传到 `/opt/longfor-checkin/longfor-checkin-sms-both-release.zip`。实际服务目录仍是 `/opt/longfor-checkin/longfor-checkin`。

## 升级（服务器）

沿用已提供的 PG 接入脚本。已接入 PG 时备份后更新代码，不再次导入旧 SQLite；未接入时会完成之前要求的专用 PG 库接入和迁移。需要 HR 的 PostgreSQL 容器正在运行，详细行为见 [PG-DEPLOY.md](PG-DEPLOY.md)。

完整复制执行，包括外层括号：

```bash
(
set -e
release_dir="$(mktemp -d /opt/longfor-checkin/longfor-checkin-sms-both-release.XXXXXX)"
python3 -m zipfile -e /opt/longfor-checkin/longfor-checkin-sms-both-release.zip "$release_dir"
python3 "$release_dir/longfor-checkin/scripts/connect_pg.py"
)
```

脚本只更新签到项目，保留原数据与加密密钥，先备份、失败自动恢复。它不会重启 HR 或修改 HR 表；两应用仍共用 PG 实例资源。如果无法唯一找到 HR 的 PG 容器，按输出指引用 `--pg-container 实际容器名` 重试。

```bash
cd /opt/longfor-checkin/longfor-checkin
docker compose -p longfor-checkin ps
curl --noproxy '*' --max-time 10 http://127.0.0.1:8091/health
```

响应应含 `"status":"ok"` 和 `"storage":"postgresql"`。刷新网页，管理员与普通用户密码继续沿用。

## 网页短信使用

1. 登录自己的本站账号，进入「登录凭证」。
2. 首次上传自己 App 成功登录的 HAR，需包含成功响应，最好一直记录到打开签到页。看到“已配置自己的 App 登录设备”即可使用短信入口。
3. 选择仅更新 App、仅更新小程序，或同时更新两端。**同时更新会让两端使用本次手机号对应的同一龙湖账号**；两端账号不同请分别更新。
4. 输入手机号，主动点击“获取验证码”，填写新收到的 6 位验证码，点击“验证并更新”。程序分别查询所选渠道，全部通过才保存；任一失败保留两端旧凭证。
5. 原有各端签到设备信息各自保留。如果提示缺少设备信息，需再导入对应签到页 HAR。原生登录 fingerprint 不能代替签到设备 token。

验证码由龙湖 App 短信接口发送，小程序更新利用同账号登录态经查询验证后的兼容性，不需要填写微信 thirdId。本次已真实验证 App token 被小程序状态接口接受，但没有发送新短信或提交小程序签到做完整联调；未来服务端策略变化时会查询失败并保留旧配置。遇到手机风控请回手机处理，或在微信登录后上传小程序成功登录 HAR。

每天 09:10 签到、16:00 复核补签以及 QQ／163 邮件通知按原计划继续。本次升级不会自动开启暂停的计划，也不会自动请求真实短信。云服务器需执行以上更新才会生效。

协议与验证边界见 [MINI-LOGIN-ANALYSIS.md](MINI-LOGIN-ANALYSIS.md)。
