# 现场 HTTPS 入口

- 后台与 API：`https://fitlean-api.mingyuefanxing.top`
- PC 用户管理：`https://fitlean-api.mingyuefanxing.top/admin/users`
- Nginx 使用现有 `hr-welcome-nginx` 容器，保留 HR 的 `8088:80`，新增签到入口 `80:8080` 与 `443:443`。
- 配置：`/opt/longfor-checkin/https/fitlean-api.conf`；HR Compose 增补文件：`/opt/hr-welcome/app/deploy/docker/compose.override.yml`。日后更新 HR 时需保留此增补文件。
- 签到上游使用 `longfor-checkin_default` 网络的 `checkin:8080`，由 Docker DNS 动态解析，支持应用容器重建。
- Nginx 在该网络固定为 `172.19.0.254`；应用通过 `TRUSTED_PROXY_IPS=172.19.0.254` 仅信任此地址的转发 IP 和 HTTPS 协议。直连请求的伪造转发头无效。更换网络地址时须同步调整此配置。
- HTTPS 入口为会话 Cookie 添加 Secure、HttpOnly 与 SameSite=Strict，原 HTTP 8091 入口继续兼容已有客户端。

## 证书和续期

用户提供的旧证书已于 2026-06-27 过期，未用于公网入口。新证书由 Let's Encrypt 签发，首次有效期至 2027-01-25。

证书目录：`/opt/longfor-checkin/https/letsencrypt`。私钥仅由服务器 root 和 Nginx 主进程读取。

专用 `fitlean-certbot-renew.timer` 每天检查两次，续期成功后通过 `renewal-hooks/deploy/reload-nginx` 校验并重载 Nginx。ACME 验证路径 `/.well-known/acme-challenge/` 保留 HTTP 访问，其余 HTTP 请求重定向到 HTTPS。

```sh
systemctl status fitlean-certbot-renew.timer
journalctl -u fitlean-certbot-renew.service
certbot certificates --config-dir /opt/longfor-checkin/https/letsencrypt
```

## 发布与恢复

发布前代码和原配置位于 `/opt/longfor-checkin/backup-before-20261027`；一致性数据库备份位于 `/opt/longfor-checkin/longfor-checkin-pg-backup.cqf5ze8r`，包含 PG dump、匹配的加密密钥及配置。旧镜像保留为 `longfor-checkin:rollback-20261027`。

当前版只增加表和设置，原用户、密码、凭据与计划沿用。不要将旧 SQLite 数据覆盖到当前 PostgreSQL。

应用回退时先停止签到服务并备份最新数据，恢复原代码与 Compose 配置，使用旧镜像重新启动签到服务。仅需代码回退时无需恢复旧数据库；若需数据恢复，必须同时使用同一次备份的 PG dump 和加密密钥，并另行评估备份后的新增数据。

HR 的数据库和 API 容器无需重建。网关回退时恢复 HR 原 Compose 配置并停用新增的增补文件，再仅重建 nginx 服务；这会移除签到域名的 HTTPS 入口。
