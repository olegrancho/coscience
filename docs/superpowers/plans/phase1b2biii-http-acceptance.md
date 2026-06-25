# Phase 1b-2b-iii — HTTP API / container manual acceptance

Prereq: Docker + docker compose installed; run from the repo root.

1. Build & start:  `docker compose up --build -d`
2. Health:         `curl -s localhost:8000/health`           -> {"status":"ok"}
3. Submit a sprint:
   curl -s -X POST localhost:8000/sprints -H 'content-type: application/json' \
     -d '{"id":"sp1","goals":"smoke","plan":[{"id":"s1","run":"echo hi"}]}'
   -> 201, returns the created sprint detail (status "proposed")
4. List proposed:  `curl -s 'localhost:8000/sprints?status=proposed'`  -> [{"id":"sp1",...}]
5. Approve:        `curl -s -X POST localhost:8000/sprints/sp1/approve` -> status "approved"
6. Ledger:         `curl -s localhost:8000/ledger`  -> {"capacity":...,"used":...,"available":...,"leases":[...]}
7. 404 check:      `curl -s -o /dev/null -w '%{http_code}' localhost:8000/sprints/nope`  -> 404
8. Persistence:    the sprint is on disk under ./data (mounted at /data). `ls ./data/sprints/sp1`.
9. Interactive docs: open http://localhost:8000/docs (FastAPI Swagger UI).
10. Tear down:     `docker compose down`
