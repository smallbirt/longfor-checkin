# 统一 HAR 与配置修改流程部署

此版按渠道合并登录与设备 HAR 入口，已有配置显示摘要并支持修改、取消。App 短信只更新 App，小程序使用本人原生登录 HAR。保留已有账号、设备、邮箱、计划和记录。

已接入 PostgreSQL 的服务器沿用既有升级脚本：先备份代码、完整数据卷和签到库，再切换签到容器；不重新导入旧 SQLite，不重启 HR。脚本须能识别现有 HR 的 PostgreSQL 容器。

上传文件：`longfor-checkin-config-flow-20261020-release.zip`，服务器位置为 `/opt/longfor-checkin/`。

在服务器执行完整代码块：

```bash
(
set -e
umask 077
release_zip=/opt/longfor-checkin/longfor-checkin-config-flow-20261020-release.zip
test -f "$release_zip"
python3 -m zipfile -t "$release_zip"
release_dir="$(mktemp -d /opt/longfor-checkin/longfor-checkin-config-flow.XXXXXX)"
python3 -m zipfile -e "$release_zip" "$release_dir"
python3 "$release_dir/longfor-checkin/scripts/connect_pg.py" \
  --app-dir /opt/longfor-checkin/longfor-checkin \
  --project longfor-checkin
)
```

原 `.env`、端口、管理员密码与数据继续沿用。不要运行 setup、恢复旧数据包或删除数据卷。如果无法识别 PG 容器，依据脚本提示指定实际容器名；不要启动新的数据库代替现有库。

完成后检查：

```bash
cd /opt/longfor-checkin/longfor-checkin
docker compose -p longfor-checkin ps
curl --noproxy '*' --max-time 10 http://127.0.0.1:8091/health
```

健康响应应包含 `"status":"ok"` 和 `"storage":"postgresql"`。原账号登录后，在「龙湖账号」确认每个渠道只有一个 HAR 导入入口；已有配置点击「更新登录」展开。在「通知与账号」「平台设置」确认原邮箱设置保留。升级本身不发送测试短信、邮件或手动签到，原已启用的日常计划继续运行。

本版回归共 186 项通过，页面交互使用模拟 API。远程部署完成与否以实际连接、容器健康检查和线上页面检查为准。
