# 自动选用个人／平台设备版更新

本版移除浏览器 SDK 初始化试用入口和设备方式下拉框。用户自己导入的设备优先，缺少的部分用管理员配置；管理员导入会自动同步到平台。登录失效与设备信息是否保存分开显示。

## 1. 上传代码包

将 `longfor-checkin-device-priority-release.zip` 上传到服务器：

```text
/opt/longfor-checkin/longfor-checkin-device-priority-release.zip
```

只上传新代码包，不覆盖现有 `.env`、数据卷、密钥或数据库。另一个 `longfor-device-defaults.json` 留在自己电脑上，稍后通过管理员页面上传即可。

## 2. 服务器执行

完整复制，包括外层括号：

```bash
(
set -e
umask 077
release_zip=/opt/longfor-checkin/longfor-checkin-device-priority-release.zip
test -f "$release_zip"
python3 -m zipfile -t "$release_zip"
release_dir="$(mktemp -d /opt/longfor-checkin/longfor-checkin-device-priority.XXXXXX)"
python3 -m zipfile -e "$release_zip" "$release_dir"
python3 "$release_dir/longfor-checkin/scripts/connect_pg.py" \
  --app-dir /opt/longfor-checkin/longfor-checkin \
  --project longfor-checkin
)
```

已切换 PG：脚本备份 PG 与加密密钥后升级代码，不重复导入旧 SQLite。

尚未切换 PG：脚本备份原代码、数据和密钥，把 SQLite 迁入 HR 实例的独立 `longfor_checkin` 库，逐行核对后切换签到服务。

脚本需要 HR 的 PG 容器正常运行，默认查找 `hr-welcome` 的 `db` 服务；不能唯一找到时按提示指定 `--pg-container 实际容器名`。脚本只切换签到容器，不重启 HR；失败时尝试回退原签到服务。不要执行 `setup`、覆盖 `.env`、导入旧数据包或删除数据卷。

## 3. 检查并补充平台登录设备

```bash
cd /opt/longfor-checkin/longfor-checkin
docker compose -p longfor-checkin ps
curl --noproxy '*' --max-time 10 http://127.0.0.1:8091/health
```

应包含 `"status":"ok"` 与 `"storage":"postgresql"`。使用原管理员账号密码登录。

到「平台设置 → 平台设备配置」，查看是否缺少 App 短信登录设备。如果缺少，选择本次单独提供的 `longfor-device-defaults.json`，点击「导入平台设备配置」。只补设备参数，不复制任何账号登录态；云端管理员已导入的签到设备优先保留。设备专用文件含敏感设备信息，仅供本平台管理员使用，不放公开目录。

回「登录凭证」，应该直接看到使用的平台或个人设备，无设备下拉框或 SDK 初始化按钮。新用户使用自己的手机号和短信验证码登录；自行导入个人设备后，对应部分立即优先使用个人配置。

如果显示“登录失效”，之前保存的账号 Token 已过期，仍需本人新短信或手机登录更新，不能靠设备配置恢复过期账号凭证。平台设备跨账号兼容性及实际签到仍需实际确认。
