## 2026-10-27：正式 HTTPS API 已接通

后端 API 基址为 `https://fitlean-api.mingyuefanxing.top`（不加 `/api`）。小程序请求层自行拼接 `/api/session`、`/api/state`、`/api/import` 等路径，继续使用原平台账号、Cookie 和 CSRF。该域名同时提供 PC 页面，API 路径直接返回 JSON。

小程序构建默认值和最新 `dist/` 已切到此 HTTPS 域名，覆盖下文旧的 HTTP/本地构建说明。需要本地调试时仍可通过 `CHECKIN_API_BASE_URL` 显式覆盖。微信后台 request/uploadFile/downloadFile 合法域名按需配置同一域名。重新编译开发者工具后，须使用线上平台账号登录；原本地服务账号数据不会自动同步到服务器。本次未上传或提交小程序版本。

## 小程序订阅推送（2026-10-26 更新）

已取消企业微信，增加小程序下一次定时复核订阅推送；详情见 [MINI-PUSH-SETUP.md](MINI-PUSH-SETUP.md)。新增 src-checkin/shared/SubscribeCard.tsx、push.ts 和 pages/result/index 页面；用户需主动点击订阅，wx.login 仅绑定通知身份，不替代本站登录或龙湖授权。管理员配置入口已完成，但还没有订阅模板与 AppSecret，因此尚未验证真实收件。

后台新增 /api/admin/mini-push、/api/mini-push 及 prepare、subscribe、pause、unbind 接口，和按本站用户隔离的 /api/jobs/<id> 结果页接口。SQLite/PG 启动时自动增加推送表；无需重建数据库、用户或加密密钥，迁移工具兼容新表。小程序通知卡片登录过期后可登录回到结果页。当前本地运行仍关闭定时和后台投递。

本次全量回归 239 项：219 通过，20 项 PG 专项未配置测试库跳过；新增推送专项 21 项通过。小程序类型检查、构建及原生订阅流程替代传输检查通过。旧版本说明如下，涉及“无需 AppSecret”“仅邮件”等描述仅适用于推送接入之前。

# 微信小程序共用现有签到服务

2026-10-26：现有 Taro 小程序（`/Users/wpy/project/fitness/frontend/apps/weapp`）已接入本服务的用户、真实多账号、计划、记录、HAR 导入、App 短信更新和邮箱通知。原健身服务不再参与。

新增 `/api/session`、`/api/session/login`、`/api/session/register`、`/api/session/logout`，全部复用现有 Web 会话和 CSRF，无新数据库、无微信 AppSecret、无第二套用户身份。Web 登录与原 API 兼容；不读取小程序提交的 user_id 来切换空间。登录尝试频率与网页端共用，退出和改密按现有版本校验生效。

小程序默认开发地址 `http://120.55.246.247:8091`，正式版需要换为 HTTPS。没有把旧健身后端域名带入小程序构建产物。正式版在请求前拒绝 HTTP，避免将开发配置误当成正式配置。

## 当前本地运行（真实接口）

已使用 `scripts/run_local.py` 启动在 `http://127.0.0.1:18091`。使用原 `data/` SQLite 和原 `.env` 管理员密码，不读取云端 PG；真实登录、状态接口与健康检查通过。定时调度及后台邮件投递关闭，手动签到、HAR 校验、短信和邮箱操作调用真实服务。

重新启动：

```bash
cd /Users/wpy/Documents/ChatGPT/longfor-checkin
.venv/bin/python scripts/run_local.py
```

启动前需确保端口空闲。原本地数据和配置备份在 `/Users/wpy/Documents/ChatGPT/longfor-checkin-local-backup-20261026-104231`，含密钥，仅本机保管。

本次小程序 `dist/` 已用 `CHECKIN_API_BASE_URL=http://127.0.0.1:18091` 编译。开发者工具的本地域名检查已关闭以便调试；正式版仍禁止 HTTP。真机应换成本机在局域网中可访问的服务地址并按需调整监听方式，当前后端只监听本机回环地址。

会话专项现为 9 项通过（新增真实本地模式区别于模拟预览的检查）。此前整体回归为 197 项通过、20 项 PG 专项跳过。原生请求层 19 项和浏览器页面 12 项检查使用独立模拟后端完成，随后已关闭模拟服务，未对本地真实数据执行签到、短信或邮件测试。

## 后端上线

本地完成代码不表示云端已更新。2026-10-26 只读检查现有服务器 `/api/session` 返回 404，说明尚未部署本次小程序登录接口。云端需升级当前完整的多账号 Web 版本与本次会话接口。部署路径仍为 `/opt/longfor-checkin/longfor-checkin`，Compose 项目 `longfor-checkin`，服务 `checkin`。如果目前已运行多账号版本，本次服务端代码变化只在 `app/__init__.py`，另有测试文件。

部署前备份专用签到 PostgreSQL、完整数据卷（含 secret.key）和代码；保持服务器原 `.env`、Compose 配置、数据库 URL、ADMIN_PASSWORD、端口和加密密钥。使用完整公开代码包更新 app/ 和必要依赖，再仅重建 `checkin` 服务。不要重建 HR、重启 Docker 或覆盖数据库配置。Docker Hub 若仍不可达，请沿用之前已成功的基础镜像地址或缓存。

不需要数据库迁移或重建用户。后端升级后 `/health` 应正常，`/api/session` 应返回 JSON（未登录时 user 为 null）。随后从小程序使用网页端同一个平台账号登录，账号、邮箱与计划应一致。

浏览器 /api/session 读取到的 CSRF 不能当成后台服务凭证分享；真实 Cookie、平台密码、HAR、SMTP 授权码均不包含在发布包中。

## 本地验证

```bash
.venv/bin/python -m unittest discover -s tests -p test_native_session.py -v
```

会话专项 8 项通过。全量 217 项中 197 项通过，20 项专用 PostgreSQL 测试因未连接测试库跳过；本次没有改动数据库适配层或表结构。

开发模拟仍可运行 `scripts/preview_web.py`，端口 18091；模拟用户 demo / demo-password-2026。所有第三方请求都使用模拟实现，调度关闭，勿当成真实签到验证。

完整小程序使用与构建说明见 `/Users/wpy/project/fitness/frontend/CHECKIN-MINIAPP.md`。
