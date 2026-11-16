# 云服务器升级

**2026-10-23 Web 多账号版：[WEB-MULTIACCOUNT.md](WEB-MULTIACCOUNT.md)。本地代码已实现；未在本次任务部署云端。**

**历史统一 HAR 与配置修改版请使用 [CONFIG-FLOW-DEPLOY.md](CONFIG-FLOW-DEPLOY.md)。下列其他版本说明仅供历史参考。**

**2026-10-20 登录更新版请使用 [LOGIN-DEPLOY.md](LOGIN-DEPLOY.md)。**

**接入 PostgreSQL 请使用 [PG-DEPLOY.md](PG-DEPLOY.md) 与 `scripts/connect_pg.py`。下文保留为 SQLite 多用户版本的升级说明，PG 接入完成后不要再按旧 SQLite 流程恢复数据。**

## SQLite 多用户版本

实际服务目录：`/opt/longfor-checkin/longfor-checkin`。
上传压缩包的位置：`/opt/longfor-checkin/longfor-checkin-multiuser-release.zip`（服务目录的上一层）。
Compose 项目名为 `longfor-checkin`，服务名为 `checkin`。

此次增加用户账号表，并在数据卷的 `users/<用户编号>/` 下保存各用户数据。旧数据保留在原位置，归 `admin` 管理员账号；原密码仍为 `.env` 中的 `ADMIN_PASSWORD`。原 QQ／163 发件配置沿用为平台发信账号，原收件邮箱归管理员。

## 1. 本地 Mac 上传

```bash
scp /Users/wpy/Documents/ChatGPT/longfor-checkin-multiuser-release.zip root@120.55.246.247:/opt/longfor-checkin/longfor-checkin-multiuser-release.zip
```

如果使用文件管理器上传，请放到上面的完整位置，保留此文件名。

## 2. 服务器：检查、备份代码、构建

完整复制下面整段，包括最外面的括号。构建失败时停止后续步骤，原容器继续运行。

```bash
(
set -e
umask 077
cd /opt/longfor-checkin/longfor-checkin
release_zip=/opt/longfor-checkin/longfor-checkin-multiuser-release.zip
python3 -m zipfile -t "$release_zip"
upgrade_dir="$(mktemp -d /opt/longfor-checkin/longfor-checkin-multiuser.XXXXXX)"
python3 -m zipfile -e "$release_zip" "$upgrade_dir"
test -f "$upgrade_dir/longfor-checkin/app/accounts.py"
# 部署包只含公开代码，确保容器的非 root 用户可以读取
chmod -R a+rX "$upgrade_dir/longfor-checkin"

backup_dir="$(mktemp -d /opt/longfor-checkin/longfor-checkin-before-multiuser.XXXXXX)"
mkdir "$backup_dir/code"
cp -a app requirements.txt Dockerfile manage.py compose.yaml "$backup_dir/code/"
if [ -f .dockerignore ]; then cp -a .dockerignore "$backup_dir/code/"; fi
if [ -f .env ]; then cp -a .env "$backup_dir/code/"; fi
container_id="$(docker compose -p longfor-checkin ps -q checkin)"
test -n "$container_id"
docker inspect --format '{{.Image}}' "$container_id" > "$backup_dir/image-id"
# 保存本次备份路径，下一步使用
printf '%s\n' "$backup_dir" > .multiuser-backup-path

cp -a "$upgrade_dir/longfor-checkin/app/." ./app/
for filename in .dockerignore requirements.txt Dockerfile manage.py README.md UPGRADE.md VERIFICATION.md LOGIN-OPTIONS.md; do
  cp "$upgrade_dir/longfor-checkin/$filename" "./$filename"
done

docker compose -p longfor-checkin build \
  --build-arg PYTHON_BASE_IMAGE=public.ecr.aws/docker/library/python:3.12-slim \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
  checkin
)
```

## 3. 服务器：备份完整数据卷，再切换容器

第 2 步显示构建成功后执行。本步会短暂停止签到服务，以便一致备份数据库、加密密钥和所有用户目录；不会停止 HR 或重启 Docker。

```bash
(
set -e
umask 077
cd /opt/longfor-checkin/longfor-checkin
backup_dir="$(cat .multiuser-backup-path)"
test -d "$backup_dir/code"
container_id="$(docker compose -p longfor-checkin ps -q checkin)"
test -n "$container_id"
docker compose -p longfor-checkin stop checkin
# 如果备份失败，先启动原容器，再退出；不要继续升级
if ! docker cp "$container_id:/data" "$backup_dir/data"; then
  docker start "$container_id"
  exit 1
fi
chmod -R go-rwx "$backup_dir/data"
docker compose -p longfor-checkin up -d --no-deps checkin
docker compose -p longfor-checkin ps
)
```

不要重新运行 setup、覆盖 `.env` 或 `compose.yaml`，不要导入过期数据包，也不要执行 `down -v`。数据备份包含账号凭证，保留在服务器的受限目录，不要公开上传。

启动后等几秒检查：

```bash
curl --noproxy '*' --max-time 10 http://127.0.0.1:8091/health
```

预期 `{"status":"ok"}`。如果失败：

```bash
cd /opt/longfor-checkin/longfor-checkin
docker compose -p longfor-checkin logs --tail=80 checkin
```

## 4. 页面确认

1. 旧登录会话需重新登录：用户名 `admin`，密码为原来的 `ADMIN_PASSWORD`。
2. 首页确认 App、小程序凭证、定时开关和原记录仍在；点击「查询两端状态」。
3. 「平台设置」确认发件邮箱仍在；这里仅管理员可见，可开放或关闭注册。
4. 普通用户自行注册独立账号，进入「通知与账号」填写自己的邮箱、接收验证码并确认，无需提供邮箱授权码。再到「登录凭证」导入自己的 App 和小程序请求。
5. 首页只展示最近 5 条记录，「查看更多」进入完整记录页，每页 20 条。
6. 原 09:10 签到、16:00 复核计划保持。新账号默认关闭计划，完成配置后自行开启。

注册默认开放，最多 100 个账号。请为多人访问配置 HTTPS 域名，之后将 `.env` 中 `COOKIE_SECURE=true` 并重建签到容器；直接通过 HTTP 访问时不能开启此项，否则登录 Cookie 无法正常发送。

## 普通用户忘记密码

管理员在服务器执行，按提示输入新密码（不回显）：

```bash
cd /opt/longfor-checkin/longfor-checkin
docker compose -p longfor-checkin exec checkin python manage.py reset-password 用户名
```

旧会话自动失效。管理员自身密码仍在 `.env` 修改 `ADMIN_PASSWORD` 后重建容器。

## 回退

新增用户表和目录不会覆盖旧数据结构。若仅回退代码，旧版只处理管理员原数据，新用户数据保留但无法在旧版访问或执行计划。不要把升级前的数据库直接覆盖回去，否则会丢失升级后新用户和运行记录。

如果需要回退，先停止签到容器，使用 `.multiuser-backup-path` 记录目录中的 `code/` 恢复旧 `app`、`requirements.txt`、`Dockerfile` 和 `manage.py`（先把当前 `app` 移到另一备份目录，再复制旧 `app`，避免混合文件）。按构建命令重建并启动。保留当前数据卷；完整数据恢复应单独决定。`image-id` 还记录了切换前镜像 ID，未清理镜像时可用于恢复旧镜像。
