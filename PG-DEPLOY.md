# 接入 HR 现有 PostgreSQL

此版本支持 PostgreSQL，并保留 SQLite 作为本地和回退模式。服务器接入脚本 `scripts/connect_pg.py` 只依赖服务器自带 Python 3 和 Docker Compose，不需要在宿主机安装 Python 驱动或 psql。

## 上传与执行

本地 Mac：

```bash
scp /Users/wpy/Documents/ChatGPT/longfor-checkin-pg-release.zip root@120.55.246.247:/opt/longfor-checkin/
```

服务器：完整执行下面整段。

```bash
(
set -e
release_dir="$(mktemp -d /opt/longfor-checkin/longfor-checkin-pg-release.XXXXXX)"
python3 -m zipfile -e /opt/longfor-checkin/longfor-checkin-pg-release.zip "$release_dir"
python3 "$release_dir/longfor-checkin/scripts/connect_pg.py"
)
```

默认签到项目目录为 `/opt/longfor-checkin/longfor-checkin`、Compose 项目名为 `longfor-checkin`。如有差异可传 `--app-dir` 和 `--project`。脚本自动查找 Compose 项目 `hr-welcome` 的 `db` 服务；未唯一找到时先执行 `docker ps --format 'table {{.Names}}\t{{.Image}}'`，然后重新执行脚本并加 `--pg-container 实际PG容器名`。

## 自动处理的内容

1. 创建内部 Docker 网络 `longfor-checkin-pg`，把现有 PG 容器接入并设置 `longfor-postgres` 别名。保留 PG 原网络，不公开 5432 端口。
2. 在现有 PG 实例创建专用数据库、账号 `longfor_checkin`。账号不是超级用户，没有建库、建角色和绕过行权限能力，最多 8 个连接；应用池默认最多 5 个连接，另有 1 个调度器锁连接。
3. 密码随机生成，仅保存到签到项目的 `.pg-bootstrap.json` 和最终 `.env`，不输出到终端。复用原配置时不会擅自重置同名角色密码；不符合归属的已有角色/数据库会停止接入。
4. 备份原代码、配置与镜像 ID。构建成功并验证 PG 可连接后，仅停止签到容器，完整复制 `/data`（管理员和各用户 SQLite、加密密钥、记录、队列）。
5. 离线迁移账号、加密凭证、邮箱配置、定时开关、运行记录和邮件队列到 PG，逐行读回核对，重置自增序列。所有数据写入同一事务；失败不删除旧 SQLite，也不合并已有 PG 业务数据。
6. 写入签到项目 `.env` 的 `DATABASE_URL`、`PG_POOL_MAX`，创建自动加载的 `compose.override.yaml`，启动并检查存储后端与调度线程。其他原有环境配置继续沿用。
7. 中途失败时自动恢复旧代码、原环境和镜像并启动原签到服务。新建的 PG 库和角色保留用于检查，不自动删除，也不覆盖源数据。

脚本不会停止或重启 HR、修改 HR 表和迁移 HR 数据。检查 HR 表权限时只读查询；如果现有 HR 把表权限授给 PUBLIC、导致新角色也能访问，脚本会停止并提示。两个应用仍共用 PG 实例的 CPU、内存和实例故障范围。

如果项目已有自定义 `compose.override.yaml`，脚本会拒绝覆盖，需先将专用网络配置合并进去。已有本脚本生成的文件可重复使用。已接入 PG 后重新执行脚本会备份 PG 和密钥、跳过旧 SQLite 导入，不会把旧数据覆盖回 PG。

## 检查结果

```bash
cd /opt/longfor-checkin/longfor-checkin
docker compose -p longfor-checkin ps
curl --noproxy '*' --max-time 10 http://127.0.0.1:8091/health
```

预期响应包含 `"status":"ok"` 和 `"storage":"postgresql"`。

登录名：旧管理员为 `admin`，密码仍是原 `ADMIN_PASSWORD`。原单用户版本也可直接升级，管理员原凭证、发信配置和记录会保留；普通用户继续自行注册。

## 数据与备份

PG 中每个用户在独立 schema `checkin_u<用户编号>` 保存设置、运行记录和通知队列，账号与平台设置在 `checkin_u1`。应用使用同一个受限角色，通过已验证的本站会话选择用户空间。普通用户无法指定其他人的空间。

加密密钥仍在原 `/data/secret.key` 和 `/data/users/<编号>/secret.key`，PG 保存的是原有加密内容。**不能删除原数据卷；备份必须同时保留 PG 数据与完整密钥目录。** 仅复制 PG dump 无法解密凭证。原 SQLite 保留为迁移时的历史快照，PG 启用后不会再同步更新。

接入脚本生成的备份目录是 `/opt/longfor-checkin/longfor-checkin-before-pg.*`；其中有 `code/`、`data/`、镜像 ID。PG 模式下再次执行还会生成 `postgres.dump`。备份含私人数据，不要公开上传。

后续备份可用 `scripts/backup_pg.py`，它仅短暂停止签到服务，复制完整数据卷并导出签到专用库，完成后恢复签到服务，不停止 HR。完整恢复需要 PG dump 与同次备份的 data 目录配套，不要单独用旧 SQLite 覆盖 PG 后的新数据。

## HR 的 PG 容器重建之后

普通容器重启保留网络；删除并重建 HR PG 容器会丢失手工接入的额外网络连接。此时执行：

```bash
cd /opt/longfor-checkin/longfor-checkin
python3 scripts/connect_pg.py --repair-network
```

它重新识别 PG、恢复专用网络连接，并只重启签到服务。HR 容器与数据库无需重启。未来如希望网络声明随 HR Compose 长期保留，可在 HR 运维配置中合并外部网络声明后按 HR 自身发布流程更新。

## 回退说明

接入过程失败会自动回退；若成功运行一段时间后再决定回到 SQLite，不能直接删除 DATABASE_URL，因为旧 SQLite 已不再更新。应先备份当前 PG 与密钥，再执行单独的数据反向迁移。不要丢弃新用户和升级后的运行记录。
