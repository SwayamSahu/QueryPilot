#!/bin/sh
set -e
# 1. Wait for the database (DNS + TCP + login). If the configured host can't be resolved
#    (broken Docker DNS / stale network), fall back to QP_DB_FALLBACK_HOSTS and rewrite the DSNs.
# 2. First start: apply the schema if it is missing and seed the demo dataset if it is empty.
ENV_OUT=/tmp/qp_db_env
rm -f "$ENV_OUT"
python - <<'PY'
import os, socket, subprocess, sys, time
from urllib.parse import urlsplit
import psycopg
from psycopg import errors

KEYS = ("QP_DB_ADMIN_URL", "QP_DB_READER_URL", "QP_DB_APP_URL")
admin = os.environ.get("QP_DB_ADMIN_URL", "postgresql+psycopg://querypilot:querypilot_dev@db:5432/querypilot")
u = urlsplit(admin.replace("postgresql+psycopg://", "postgresql://"))
host, port = u.hostname, u.port or 5432
fallbacks = [h.strip() for h in os.environ.get("QP_DB_FALLBACK_HOSTS", "").split(",") if h.strip()]
deadline = time.time() + int(os.environ.get("QP_DB_WAIT_SECONDS", "90"))

def resolves(h):
    try:
        socket.getaddrinfo(h, port, type=socket.SOCK_STREAM)
        return True
    except socket.gaierror:
        return False

def tcp_ok(h):
    try:
        socket.create_connection((h, port), timeout=3).close()
        return True
    except OSError:
        return False

def diagnose():
    print("---- network diagnosis -------------------------------------------", flush=True)
    try:
        print("/etc/resolv.conf:\n" + open("/etc/resolv.conf").read().strip())
    except OSError:
        pass
    for h in [host] + fallbacks:
        print(f"  {h!r}: resolves={resolves(h)} tcp:{port}={resolves(h) and tcp_ok(h)}")
    print("Fix: run  docker compose down --remove-orphans && docker compose up --build --force-recreate")
    print("     and see README > Troubleshooting (VPN/firewall software often breaks Docker DNS).")
    print("------------------------------------------------------------------", flush=True)

chosen, diagnosed = None, False
while time.time() < deadline:
    for h in [host] + fallbacks:
        if resolves(h) and tcp_ok(h):
            chosen = h
            break
    if chosen:
        break
    if not diagnosed and not resolves(host):
        print(f"cannot resolve database host {host!r}; trying fallbacks {fallbacks} while waiting ...", flush=True)
        diagnose()
        diagnosed = True
    time.sleep(2)
if not chosen:
    diagnose()
    sys.exit(f"database not reachable at {host}:{port} (or fallbacks {fallbacks})")

if chosen != host:
    print(f"WARNING: using fallback database host {chosen!r} instead of {host!r}", flush=True)
    with open("/tmp/qp_db_env", "w") as fh:
        for k in KEYS:
            if os.environ.get(k):
                v = os.environ[k].replace(f"@{host}:", f"@{chosen}:")
                os.environ[k] = v
                fh.write(f"export {k}='{v}'\n")

if os.environ.get("QP_SEED_ON_START", "true") != "true":
    sys.exit(0)

from querypilot.config import get_settings  # noqa: E402  (after env rewrite)
get_settings.cache_clear()
dsn = get_settings().db_admin_url.replace("postgresql+psycopg://", "postgresql://")
state = None
while time.time() < deadline + 30:
    try:
        with psycopg.connect(dsn, connect_timeout=5) as c:
            state = c.execute("SELECT count(*) FROM sales.orders").fetchone()[0]
        break
    except errors.UndefinedTable:
        state = "no_schema"
        break
    except psycopg.OperationalError as e:  # e.g. still starting / auth not ready
        print("waiting for database:", str(e).strip().splitlines()[0], flush=True)
        time.sleep(2)
if state is None:
    sys.exit("database accepted TCP but login kept failing; check POSTGRES_PASSWORD matches the existing volume")
if state == "no_schema":
    print("schema missing: applying db/init/01_schema.sql and seeding ...", flush=True)
    subprocess.check_call([sys.executable, "-m", "querypilot.data.seed", "--schema"])
elif state == 0:
    print("seeding demo data ...", flush=True)
    subprocess.check_call([sys.executable, "-m", "querypilot.data.seed"])
else:
    print(f"database already has {state} orders; skipping seed", flush=True)
PY
# pick up a fallback host chosen above
[ -f "$ENV_OUT" ] && . "$ENV_OUT"
exec "$@"
