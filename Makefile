.PHONY: up down llm observability db-local seed api ui test bench gate lint

up:            ## db + api + ui (rules planner; no model download needed)
	docker compose up --build
llm:           ## also start Ollama; then: docker compose exec ollama ollama pull qwen2.5-coder:7b
	QP_LLM_PROVIDER=ollama docker compose --profile llm up --build
observability: ## add Prometheus (:9090) and Grafana (:3001)
	docker compose --profile observability up --build
down:
	docker compose down

# ---- local development (Postgres 16 + pgvector running on localhost) ----
seed:
	cd backend && python -m querypilot.data.seed --schema
api:
	cd backend && QP_REFERENCE_DATE=2026-10-10 uvicorn querypilot.api.main:app --reload --port 8000
ui:
	cd frontend && npm install && npm run dev
test:
	cd backend && pytest -q
lint:
	cd backend && ruff check querypilot tests
bench:
	cd backend && python -m querypilot.eval.build_benchmark && python -m querypilot.eval.run
gate:
	cd backend && python -m querypilot.eval.gate

doctor:        ## diagnose Docker networking / startup problems
	sh scripts/doctor.sh
reset:         ## recreate containers + network from scratch (keeps nothing of the demo DB)
	docker compose down -v --remove-orphans && docker compose up --build --force-recreate
