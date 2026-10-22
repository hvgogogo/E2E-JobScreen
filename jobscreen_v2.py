#!/usr/bin/env python3
"""Job Screen v2 - command line.

    python3 jobscreen_v2.py serve [--port 8020] [--no-update]   start the website (the launcher does this)
    python3 jobscreen_v2.py update                              fetch job ads once
    python3 jobscreen_v2.py doctor                              health report (accounts, sources, backups)
    python3 jobscreen_v2.py backup                              zip all account data into data/backups/
    python3 jobscreen_v2.py upgrade                             upgrade data/ after installing a new version
    python3 jobscreen_v2.py set-password USER                   set a password (asks twice, hidden)
    python3 jobscreen_v2.py set-role USER owner|admin|user      change a role from Terminal
    python3 jobscreen_v2.py migrate [--force]                   first install only: v1 data -> owner account
"""
import getpass
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import accounts as A          # noqa: E402
from app import engine as E            # noqa: E402
from app import maintenance as M       # noqa: E402
from app.core import APP_DIR, LOCK, config  # noqa: E402


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "serve"
    if cmd == "serve":
        from app.web import serve
        port = int(os.environ.get("JOBSCREEN_PORT") or config().get("port", 8020))
        if "--port" in argv:
            port = int(argv[argv.index("--port") + 1])
        host = os.environ.get("JOBSCREEN_HOST") or config().get("host", "127.0.0.1")
        serve(port, host, "--no-update" not in argv)
    elif cmd == "update":
        M.upgrade(); E.load_ads(); print(E.run_update())
    elif cmd == "doctor":
        sys.exit(M.doctor())
    elif cmd == "backup":
        print(M.backup("manual"))
    elif cmd == "upgrade":
        print("\n".join(M.upgrade(verbose=False)) or "data already up to date")
    elif cmd == "migrate":
        M.migrate_v1(os.path.join(APP_DIR, "..", "v1"), os.path.join(APP_DIR, "seed_admin.json"), "--force" in argv)
    elif cmd == "set-password" and len(argv) > 2:
        u = argv[2].lower()
        users = A.load_users()
        if u not in users:
            sys.exit("no such account")
        p1 = getpass.getpass("new password: "); p2 = getpass.getpass("again: ")
        if p1 != p2 or len(p1) < 8:
            sys.exit("passwords differ or shorter than 8 characters")
        with LOCK:
            users = A.load_users(); users[u]["pw"] = A.hash_pw(p1); A.save_users(users)
        A.load_sessions(); A.drop_sessions(user=u)
        print("password changed; all sessions of", u, "logged out")
    elif cmd == "set-role" and len(argv) > 3:
        u, role = argv[2].lower(), argv[3]
        with LOCK:
            users = A.load_users()
            if u not in users or role not in A.ROLES:
                sys.exit("usage: set-role USER owner|admin|user")
            if role == "owner":
                for x in users:
                    if users[x].get("role") == "owner":
                        users[x]["role"] = "admin"
            users[u]["role"] = role; users[u].pop("is_admin", None)
            A.save_users(users)
        print(f"{u} is now {role}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
