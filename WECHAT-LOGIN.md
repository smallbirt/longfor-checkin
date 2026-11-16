# 微信登录与本站账号一对一绑定

小程序首页未登录时进入登录页，提供“微信快捷登录”和原账号密码登录。首次点击微信登录，只验证微信身份；没有绑定时，必须验证已有本站账号密码并点击“确认绑定并登录”。已绑定的微信可直接登录对应本站账号。

没有本站账号时可先注册，再到“我的 → 微信账号绑定”绑定当前微信。已有账号也可在此页面查看绑定时间、验证密码后绑定或解绑。解绑使其他设备会话失效，当前设备保留账号密码登录态；原签到计划、积分和订阅通知保留。推送授权不自动授予微信登录权限。

## 管理员配置

在 PC 平台设置 `/admin` 的“微信登录”中填写本小程序 AppID、AppSecret，勾选开启并保存。登录独立于订阅推送，不需要订阅消息模板；同一 AppID 可留空复用已保存的推送密钥。AppSecret 加密存储且不回显，不会进入小程序构建产物。

默认关闭微信登录，配置缺失时仍可使用原账号密码。已有绑定时不可直接更换 AppID；更换密钥或开关会撤销尚未完成的绑定票据，已有绑定关系不会因轮换密钥消失。

## 接口与数据

- `GET /api/session` 增加 `wechat_login_enabled`。
- `POST /api/session/wechat` 接收 `code`：已绑定则建立原本站会话；未绑定返回 10 分钟有效的一次性绑定票据。
- `POST /api/session/wechat/bind` 接收 `ticket`、`username`、`password`：验证并绑定后建立本站会话。
- `GET /api/wechat/binding` 返回当前用户的绑定状态和时间。
- `POST /api/wechat/binding` 接收新鲜 `code`、本站 `password`，绑定当前已登录账号。
- `POST /api/wechat/unbind` 接收本站 `password`，解绑并使其他设备会话失效。
- `GET/POST /api/admin/wechat-login` 仅管理员可访问配置，所有写操作保留 CSRF 验证。

微信 `code` 仅由后端换取身份；`session_key`、OpenID、AppSecret 不返回客户端。数据库保存 AppID 与 OpenID 的 HMAC 摘要，并对微信身份、本站用户 ID 分别建立唯一约束。一次性 code 的摘要会先被保留，避免并发重复兑换；待绑定票据只存摘要，关联发起会话的 CSRF 摘要，且在完成、解绑或配置变化时失效。

SQLite 与 PostgreSQL 使用同样的事务逻辑。`wechat_links`、`wechat_tickets`、`wechat_codes` 随启动创建，SQLite → PG 迁移包含这些表。不会创建第二套平台用户或合并不同账号的签到数据。微信登录绑定与已有推送绑定冲突时拒绝重绑定，需先在原账号解除相应绑定。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -p test_wechat_auth.py -q
.venv/bin/python scripts/preview_wechat.py
# 另一个终端，预览仅用临时 SQLite 和模拟微信身份兑换：
cd /Users/wpy/project/fitness/frontend
node apps/weapp/scripts/verify-wechat.cjs
```

真实微信兑换还需要在现场配置本小程序 AppSecret。隔离预览的微信身份与密钥都是假的，不能用来宣称真实授权链路已验证，也不能部署预览脚本作为生产入口。
