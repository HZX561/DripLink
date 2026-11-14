"""Create or list dashboard accounts. Run from the project root:

    python -B webapp/manage_users.py add-admin  alice  --display "Alice"
    python -B webapp/manage_users.py add-user   bob    --display "Bob"
    python -B webapp/manage_users.py list

Passwords are read from DRIPLINK_NEW_PASSWORD or prompted interactively.
Only the salted PBKDF2 hash is stored; no plaintext is kept on disk.
"""
import getpass, os, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
from werkzeug.security import generate_password_hash

BASE = Path(__file__).resolve().parent
DB = BASE / 'instance' / 'records.sqlite3'
ROLES = ('admin', 'user')


def connect():
    conn = sqlite3.connect(DB, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def utc_time():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def read_password():
    value = os.environ.get('DRIPLINK_NEW_PASSWORD')
    if value:
        return value
    first = getpass.getpass('新密码（至少8位）：')
    if len(first) < 8:
        raise SystemExit('密码至少8位。')
    if getpass.getpass('再次输入确认：') != first:
        raise SystemExit('两次输入不一致。')
    return first


def add_user(username, role, display):
    username = (username or '').strip()
    if not username:
        raise SystemExit('用户名不能为空。')
    if role not in ROLES:
        raise SystemExit('角色必须是 admin 或 user。')
    password = read_password()
    with connect() as conn:
        existing = conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
        if existing:
            raise SystemExit(f'用户 {username} 已存在，未做修改。')
        conn.execute(
            'INSERT INTO users (username,password_hash,role,display_name,active,created_at) VALUES (?,?,?,?,1,?)',
            (username, generate_password_hash(password), role, display or None, utc_time()),
        )
    print(f'已创建 {role} 账号：{username}')


def list_users():
    with connect() as conn:
        rows = conn.execute('SELECT id,username,display_name,role,active,created_at FROM users ORDER BY username').fetchall()
    if not rows:
        print('尚无账号；未登录的本机访问仍按管理员处理。')
        return
    for r in rows:
        state = '启用' if r['active'] else '停用'
        print(f"#{r['id']:>3}  {r['username']:<20} {r['role']:<6} {state}  {r['display_name'] or ''}")


def main(argv):
    if len(argv) < 2 or argv[1] not in ('add-admin', 'add-user', 'list'):
        print(__doc__)
        raise SystemExit(2)
    DB.parent.mkdir(parents=True, exist_ok=True)
    if argv[1] == 'list':
        list_users()
        return
    role = 'admin' if argv[1] == 'add-admin' else 'user'
    username = argv[2] if len(argv) > 2 else ''
    display = argv[argv.index('--display') + 1] if '--display' in argv and argv.index('--display') + 1 < len(argv) else None
    add_user(username, role, display)


if __name__ == '__main__':
    main(sys.argv)
