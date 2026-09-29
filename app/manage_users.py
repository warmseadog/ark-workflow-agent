"""Local-only administrator bootstrap. Never expose setup through an HTTP route."""
import argparse
from getpass import getpass
from .accounts import Accounts, AccountError
from .config import settings


def main():
    parser=argparse.ArgumentParser(description='初始化首个管理员；已有账户不会被覆盖。')
    parser.add_argument('command',choices=['init-admin'])
    parser.add_argument('--username',default='admin')
    args=parser.parse_args()
    password=getpass('初始密码（6–1024位，允许纯数字）：')
    if password != getpass('再次输入：'):
        parser.error('两次密码不一致。')
    try:
        user=Accounts(settings.storage_dir).init_admin(args.username,password)
    except AccountError as exc:
        parser.error(exc.detail)
    print('管理员已就绪：'+user['username']+'。已有素材归此初始管理员。')


if __name__=='__main__':main()
