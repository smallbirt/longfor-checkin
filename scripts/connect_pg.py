#!/usr/bin/env python3
"""Run on the Docker host. No third-party Python dependencies are required."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit, unquote

NETWORK = 'longfor-checkin-pg'
ALIAS = 'longfor-postgres'
DATABASE = ROLE = 'longfor_checkin'
MARKER = 'longfor-checkin managed PostgreSQL network override'
ROLE_MARKER = 'managed by longfor-checkin connector'


class Connector:
    def __init__(self, args):
        self.args = args
        self.app = Path(args.app_dir).resolve()
        self.source = Path(__file__).resolve().parent.parent
        self.password = ''
        self.url = ''
        self.backup = None
        self.original_id = None
        self.original_image = None
        self.original_ref = None
        self.stopped = False
        self.changed = False
        self.cutover = False
        self.environment = os.environ.copy()
        # Host shell exports must not override the project's preserved admin/port values.
        for key in ('ADMIN_PASSWORD', 'PORT', 'BIND_ADDRESS', 'COOKIE_SECURE', 'DATABASE_URL', 'PG_POOL_MAX'):
            self.environment.pop(key, None)
        self.environment['COMPOSE_PROJECT_NAME'] = args.project

    def redact(self, text):
        for secret in (self.password, self.url):
            if secret:
                text = text.replace(secret, '[已隐藏]')
        return text

    def run(self, command, text=None, stream=False, env=None):
        result = subprocess.run(command, input=text, text=True, cwd=self.app,
                                env=env or self.environment, capture_output=not stream)
        if result.returncode:
            detail = '' if stream else self.redact((result.stderr or result.stdout or '')[-3500:])
            raise RuntimeError('命令执行失败。' + ('\n' + detail if detail else ''))
        return result.stdout.strip() if not stream else ''

    def compose(self, *args, staged=False):
        command = ['docker', 'compose', '-p', self.args.project]
        if staged:
            command += ['-f', str(self.app / 'compose.yaml'), '-f', str(self.source / 'compose.pg.yaml')]
        return command + list(args)

    def psql(self, sql, database='postgres'):
        # Use the existing container's local admin socket; no HR password is copied to this app.
        shell = 'exec psql -X -q -A -t -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d "$1"'
        return self.run(['docker', 'exec', '-i', self.pg, 'sh', '-c', shell, 'psql', database], text=sql)

    def discover(self):
        if self.args.pg_container:
            self.pg = self.args.pg_container
        else:
            found = self.run(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=hr-welcome',
                              '--filter', 'label=com.docker.compose.service=db']).splitlines()
            if len(found) != 1:
                raise RuntimeError('未唯一找到 HR 的 PG 容器。请用 --pg-container 容器名指定。')
            self.pg = found[0]
        if self.psql('SELECT rolsuper FROM pg_roles WHERE rolname=current_user;') != 't':
            raise RuntimeError('现有 PG 容器的 POSTGRES_USER 不是管理员，无法自动创建独立库和账号。')

    def network(self):
        listed = self.run(['docker', 'network', 'ls', '--format', '{{.Name}}']).splitlines()
        if NETWORK in listed:
            internal = self.run(['docker','network','inspect','--format','{{.Internal}}',NETWORK])
            if internal != 'true':
                raise RuntimeError('已有同名网络不是内部网络，请先检查；脚本未断开任何现有网络。')
        if NETWORK not in listed:
            self.run(['docker', 'network', 'create', '--internal', NETWORK])
        networks = json.loads(self.run(['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', self.pg]))
        if NETWORK not in networks:
            self.run(['docker', 'network', 'connect', '--alias', ALIAS, NETWORK, self.pg])
        elif ALIAS not in (networks[NETWORK].get('Aliases') or []):
            raise RuntimeError('接入网络已存在但缺少 PG 专用别名，请检查网络连接；脚本未断开现有网络。')

    def read_environment(self):
        values = {}
        for line in (self.app / '.env').read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                values[k.strip()] = v.strip().strip('"\'')
        return values

    def credentials(self):
        values = self.read_environment()
        current = values.get('DATABASE_URL', '')
        state_path = self.app / '.pg-bootstrap.json'
        if state_path.exists():
            state = json.loads(state_path.read_text())
            self.password = state['password']
        elif current:
            parsed = urlsplit(current)
            if parsed.hostname != ALIAS or parsed.path != '/' + DATABASE or parsed.username != ROLE:
                raise RuntimeError('已有其他 DATABASE_URL，脚本拒绝覆盖。')
            self.password = unquote(parsed.password or '')
        else:
            exists = self.psql("SELECT 1 FROM pg_roles WHERE rolname='longfor_checkin';")
            if exists:
                raise RuntimeError('PG 中已有同名账号，但本项目没有其连接配置；请先确认归属，脚本不会重设密码。')
            self.password = secrets.token_urlsafe(36)
        # Connector-generated passwords are URL safe; never accept arbitrary SQL/URL content.
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]{20,128}', self.password):
            raise RuntimeError('连接凭据格式异常，请检查本项目的 PG 配置。')
        self.url = 'postgresql://' + ROLE + ':' + self.password + '@' + ALIAS + ':5432/' + DATABASE
        if current and current != self.url:
            raise RuntimeError('DATABASE_URL 与接入凭据不一致，请检查；未覆盖现有配置。')
        if not state_path.exists():
            fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump({'password': self.password}, f)
        self.already_pg = bool(current)
        self.environment['DATABASE_URL'] = self.url
        self.environment['PG_POOL_MAX'] = '5'

    def provision(self):
        # Only the dedicated role/database may be created/changed. Existing HR objects are read-only here.
        self.psql('''
DO $connector$
BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='longfor_checkin') THEN
   CREATE ROLE longfor_checkin LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 8 PASSWORD '%s';
   COMMENT ON ROLE longfor_checkin IS '%s';
 ELSIF (SELECT shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname='longfor_checkin') IS DISTINCT FROM '%s'
       OR EXISTS (SELECT FROM pg_roles WHERE rolname='longfor_checkin' AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
   RAISE EXCEPTION 'Dedicated role exists with unexpected ownership or privileges';
 END IF;
 IF EXISTS (SELECT FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba WHERE d.datname='longfor_checkin' AND r.rolname<>'longfor_checkin') THEN
   RAISE EXCEPTION 'Dedicated database exists with a different owner';
 END IF;
END
$connector$;
SELECT 'CREATE DATABASE longfor_checkin OWNER longfor_checkin' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='longfor_checkin')
\gexec
ALTER ROLE longfor_checkin CONNECTION LIMIT 8;
REVOKE ALL ON DATABASE longfor_checkin FROM PUBLIC;
''' % (self.password, ROLE_MARKER, ROLE_MARKER))
        self.psql('REVOKE ALL ON SCHEMA public FROM PUBLIC;', DATABASE)
        hr_exists = self.psql("SELECT 1 FROM pg_database WHERE datname='hr';")
        if hr_exists:
            exposed = self.psql("""SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
                AND c.relkind IN ('r','p','v','m','f')
                AND has_table_privilege('longfor_checkin',c.oid,'SELECT,INSERT,UPDATE,DELETE');""", 'hr')
            if exposed != '0':
                raise RuntimeError('HR 库有公开表权限，新账号会继承这些权限。脚本没有修改 HR 授权，请先检查隔离配置。')

    def backup_code(self):
        self.backup = Path(tempfile.mkdtemp(prefix='longfor-checkin-before-pg.', dir=self.app.parent))
        code = self.backup / 'code'; code.mkdir()
        for name in ('app', 'scripts', '.env', '.dockerignore', 'Dockerfile', 'manage.py', 'requirements.txt',
                     'compose.yaml', '.pg-bootstrap.json', 'compose.override.yaml', 'compose.pg.yaml', 'README.md', 'UPGRADE.md', 'VERIFICATION.md', 'PG-DEPLOY.md'):
            path = self.app / name
            if path.is_dir():shutil.copytree(path, code / name)
            elif path.is_file():shutil.copy2(path, code / name)
        self.original_id = self.run(self.compose('ps', '-q', 'checkin'))
        if not self.original_id:
            raise RuntimeError('签到容器未在运行，请先确认当前服务。')
        self.original_image = self.run(['docker', 'inspect', '--format', '{{.Image}}', self.original_id])
        self.original_ref = self.run(['docker', 'inspect', '--format', '{{.Config.Image}}', self.original_id])
        self.run(['docker','image','tag',self.original_image,'longfor-checkin:rollback-' + self.backup.name.rsplit('.',1)[-1]])
        (self.backup / 'image-id').write_text(self.original_image + '\n')
        (self.backup / 'image-ref').write_text(self.original_ref + '\n')
        print('代码备份：' + str(self.backup), flush=True)

    def copy_code(self):
        self.changed = True
        if self.source == self.app:
            return
        for name in ('app', 'scripts'):
            shutil.copytree(self.source / name, self.app / name, dirs_exist_ok=True)
            for path in (self.app / name).rglob('*'):
                if path.is_dir():path.chmod(0o755)
                elif path.is_file():path.chmod(0o644)
        for name in ('.dockerignore', 'Dockerfile', 'manage.py', 'requirements.txt', 'compose.pg.yaml',
                     'README.md', 'UPGRADE.md', 'VERIFICATION.md', 'LOGIN-OPTIONS.md', 'PG-DEPLOY.md', 'LOGIN-DEPLOY.md', 'SMS-LOGIN-ANALYSIS.md', 'MINI-LOGIN-ANALYSIS.md', 'DEVICE-SETUP.md', 'DEVICE-SETUP-ANALYSIS.md', 'SHARED-DEVICE.md', 'DEVICE-PRIORITY-DEPLOY.md', 'DIAGNOSTICS.md'):
            shutil.copyfile(self.source / name, self.app / name)
            (self.app / name).chmod(0o644)

    def backup_data(self):
        self.run(self.compose('stop', 'checkin'))
        self.stopped = True
        self.run(['docker','cp',self.original_id + ':/data', str(self.backup / 'data')])
        for path in (self.backup / 'data').rglob('*'):
            path.chmod(path.stat().st_mode & ~0o077)
        if self.already_pg:
            with (self.backup / 'postgres.dump').open('wb') as f:
                result = subprocess.run(['docker','exec',self.pg,'sh','-c',
                    'exec pg_dump -U "${POSTGRES_USER:-postgres}" -d longfor_checkin -Fc'], stdout=f, stderr=subprocess.PIPE)
            if result.returncode:
                raise RuntimeError('PG 备份失败，未继续切换。')
        print('完整数据卷已备份。', flush=True)

    def save_environment(self):
        env_path = self.app / '.env'
        lines = [line for line in env_path.read_text().splitlines() if line.split('=',1)[0].strip() not in ('DATABASE_URL','PG_POOL_MAX')]
        text = '\n'.join(lines) + '\nDATABASE_URL=' + self.url + '\nPG_POOL_MAX=5\n'
        fd, temporary = tempfile.mkstemp(prefix='.pg-env-', dir=self.app)
        with os.fdopen(fd,'w') as f:f.write(text)
        os.replace(temporary, env_path)
        shutil.copyfile(self.source / 'compose.pg.yaml', self.app / 'compose.override.yaml')
        (self.app / 'compose.override.yaml').chmod(0o644)

    def wait_health(self):
        code = "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=4)); assert d.get('status')=='ok' and d.get('storage')=='postgresql'; print('PostgreSQL 服务健康')"
        for _ in range(20):
            cid = self.run(self.compose('ps','-q','checkin'))
            if cid:
                result = subprocess.run(['docker','exec',cid,'python','-c',code],capture_output=True,text=True)
                if not result.returncode:
                    print('健康检查通过：PostgreSQL，后台调度正常。', flush=True)
                    return
            time.sleep(2)
        raise RuntimeError('新版健康检查未通过。')

    def rollback(self):
        if not self.backup or not self.changed:
            return
        print('接入失败，正在恢复原签到服务。', flush=True)
        code = self.backup / 'code'
        for name in ('app','scripts'):
            current = self.app / name
            if current.exists():
                shutil.move(str(current), str(self.backup / ('failed-' + name)))
            if (code / name).exists():shutil.copytree(code / name, current)
        for name in ('Dockerfile','manage.py','requirements.txt','.dockerignore','.env','compose.pg.yaml',
                     'compose.override.yaml','README.md','UPGRADE.md','VERIFICATION.md','PG-DEPLOY.md'):
            if (code / name).exists():shutil.copy2(code / name, self.app / name)
            elif name == 'compose.override.yaml' and (self.app / name).exists():
                if MARKER in (self.app / name).read_text():(self.app / name).unlink()
        self.run(['docker','image','tag',self.original_image,self.original_ref])
        if self.stopped:
            clean = self.environment.copy();clean.pop('DATABASE_URL',None);clean.pop('PG_POOL_MAX',None)
            self.run(self.compose('up','-d','--no-deps','--no-build','checkin'), env=clean)
        print('原服务已恢复；源 SQLite 和新 PG 数据均保留，未覆盖数据卷。', flush=True)

    def install(self):
        if not (self.app / '.env').is_file() or not (self.app / 'compose.yaml').is_file():
            raise RuntimeError('请指定已有签到项目目录（需包含 .env 和 compose.yaml）。')
        override = self.app / 'compose.override.yaml'
        if override.exists() and MARKER not in override.read_text():
            raise RuntimeError('已有自定义 compose.override.yaml，脚本拒绝覆盖；请先合并网络配置。')
        self.discover()
        self.network()
        if self.args.repair_network:
            self.run(self.compose('restart','checkin'))
            self.wait_health()
            return
        self.credentials()
        self.backup_code()
        print('创建或确认签到专用数据库与账号…', flush=True)
        self.provision()
        self.copy_code()
        print('构建新版镜像，原签到容器继续运行…', flush=True)
        self.run(self.compose('build','--build-arg','PYTHON_BASE_IMAGE=' + self.args.base_image,
                 '--build-arg','PIP_INDEX_URL=' + self.args.pip_index,'checkin'),stream=True)
        self.run(self.compose('run','--rm','--no-deps','checkin','python','manage.py','check-db',staged=True),stream=True)
        self.backup_data()
        if not self.already_pg:
            print('迁移 SQLite 并逐行核对…', flush=True)
            self.run(self.compose('run','--rm','--no-deps','checkin','python','manage.py','migrate-pg',staged=True),stream=True)
        self.save_environment()
        self.cutover = True
        self.run(self.compose('up','-d','--no-deps','checkin'))
        self.wait_health()
        print('接入完成。数据库：longfor_checkin；原管理员密码、端口与邮箱配置保留。', flush=True)
        print('备份目录：' + str(self.backup), flush=True)


def main():
    parser = argparse.ArgumentParser(description='将签到系统接入 HR 现有 PostgreSQL（只重建签到容器）')
    parser.add_argument('--app-dir',default='/opt/longfor-checkin/longfor-checkin')
    parser.add_argument('--project',default='longfor-checkin')
    parser.add_argument('--pg-container',help='自动识别失败时指定现有 PG 容器名')
    parser.add_argument('--repair-network',action='store_true',help='HR PG 容器重建后，恢复专用网络并重启签到服务')
    parser.add_argument('--base-image',default='public.ecr.aws/docker/library/python:3.12-slim')
    parser.add_argument('--pip-index',default='https://mirrors.aliyun.com/pypi/simple/')
    args = parser.parse_args()
    connector = Connector(args)
    try:
        connector.install()
    except Exception as exc:
        print('错误：' + connector.redact(str(exc)),file=sys.stderr)
        try:connector.rollback()
        except Exception:
            print('自动恢复未完成。请保留备份目录并检查签到容器；HR 服务没有被停止。',file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
