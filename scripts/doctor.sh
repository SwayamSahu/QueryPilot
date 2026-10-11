#!/usr/bin/env sh
# QueryPilot Docker diagnostics. Run from the repo root:  sh scripts/doctor.sh
set +e
line() { printf '\n== %s ==\n' "$1"; }

line "Docker"
docker version --format 'client {{.Client.Version}} / server {{.Server.Version}} ({{.Server.Os}}/{{.Server.Arch}})' 2>&1
docker compose version 2>&1

line "Containers"
docker compose ps -a

line "Network querypilot_net (which containers are attached?)"
docker network inspect querypilot_net --format '{{range .Containers}}{{.Name}} -> {{.IPv4Address}}{{"\n"}}{{end}}' 2>&1

line "Can a container on the network resolve 'db' and 'api'?"
docker run --rm --network querypilot_net busybox:1.36 sh -c 'nslookup db 127.0.0.11; nslookup api 127.0.0.11' 2>&1 | grep -E "Name|Address|can't|NXDOMAIN"

line "Host-published ports"
(command -v nc >/dev/null && { nc -z localhost 5432 && echo "5432 open" || echo "5432 closed"; nc -z localhost 8000 && echo "8000 open" || echo "8000 closed"; }) || echo "(nc not installed; skip)"
curl -s -m 3 http://localhost:8000/healthz && echo || echo "API /healthz not reachable on localhost:8000"

line "Last api log lines"
docker compose logs --tail 25 api 2>&1

cat <<'TXT'

== What to do ==
* 'db'/'api' not resolvable from busybox  -> Docker's internal DNS is broken on this machine:
    docker compose down --remove-orphans && docker network prune -f && docker compose up --build --force-recreate
  If it persists: pause VPN / endpoint-security software (Zscaler, Cisco AnyConnect, GlobalProtect...)
  and restart Docker Desktop. The stack also falls back to host.docker.internal automatically.
* api log says "login kept failing"  -> the pgdata volume was created with another password:
    docker compose down -v   (deletes the demo database; it is re-seeded on next start)
TXT
