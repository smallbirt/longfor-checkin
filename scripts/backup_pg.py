#!/usr/bin/env python3
"""Consistent backup of the dedicated PG database and all encryption keys."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from connect_pg import Connector


def main():
    p=argparse.ArgumentParser(description='备份签到 PG 数据库、数据卷和配置，只短暂停止签到服务')
    p.add_argument('--app-dir',default='/opt/longfor-checkin/longfor-checkin')
    p.add_argument('--project',default='longfor-checkin')
    p.add_argument('--pg-container')
    args=p.parse_args()
    c=Connector(args); stopped=False; cid=None
    try:
        c.discover()
        c.url=c.read_environment().get('DATABASE_URL','')
        if not c.url.startswith('postgresql://longfor_checkin:'):
            raise RuntimeError('当前项目未配置签到专用 PG，未执行备份。')
        cid=c.run(c.compose('ps','-q','checkin'))
        if not cid:raise RuntimeError('签到容器未运行，请检查当前服务。')
        folder=Path(tempfile.mkdtemp(prefix='longfor-checkin-pg-backup.',dir=c.app.parent))
        config=folder/'config';config.mkdir()
        for name in ('.env','.pg-bootstrap.json','compose.yaml','compose.override.yaml'):
            if (c.app/name).exists():shutil.copy2(c.app/name,config/name)
        c.run(c.compose('stop','checkin'));stopped=True
        c.run(['docker','cp',cid+':/data',str(folder/'data')])
        with (folder/'postgres.dump').open('wb') as f:
            result=subprocess.run(['docker','exec',c.pg,'sh','-c',
                'exec pg_dump -U "${POSTGRES_USER:-postgres}" -d longfor_checkin -Fc'],stdout=f,stderr=subprocess.PIPE)
        if result.returncode:raise RuntimeError('PG 导出失败，已保留数据卷副本。')
        for item in folder.rglob('*'):
            item.chmod(item.stat().st_mode & ~0o077)
        (folder/'BACKUP_COMPLETE').write_text('PostgreSQL dump + matching data directory + configuration\n')
        print('完整备份已保存：'+str(folder))
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(1)
    finally:
        if stopped and cid:
            c.run(['docker','start',cid])


if __name__=='__main__':main()
