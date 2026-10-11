"""Runs the benchmark end to end and writes benchmark/results/latest.{json,md}.

  python -m querypilot.eval.run                     # full benchmark
  python -m querypilot.eval.run --split heldout     # only held-out phrasings
  python -m querypilot.eval.run --mlflow            # also log the run to MLflow (if installed)

Gold answers are computed by executing independent gold SQL as the table owner (bypassing
RLS, with explicit tenant/region filters). System answers go through the full production
pipeline (guard -> planner -> validator -> RLS-scoped read-only execution -> checks).
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg

from querypilot.config import BACKEND_DIR, get_settings
from querypilot.eval.gold import REFERENCE_DATE
from querypilot.eval.scoring import execution_match, result_match

QUESTIONS = BACKEND_DIR / "benchmark" / "questions.jsonl"
RESULTS = BACKEND_DIR / "benchmark" / "results"


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 2)


def rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "heldout"])
    ap.add_argument("--category")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--mlflow", action="store_true")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    s = get_settings()
    s.reference_date = date.fromisoformat(REFERENCE_DATE)
    from querypilot.pipeline.service import QueryPilot  # after settings are pinned
    from querypilot.safety.policy import get_policy
    from querypilot.semantic.retriever import retriever_for

    pilot = QueryPilot(persist=False)
    pilot.svc.executor.cache.clear()
    pol = get_policy()

    cases = [json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()]
    if args.split:
        cases = [c for c in cases if c["split"] == args.split]
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.limit:
        cases = cases[: args.limit]

    admin = psycopg.connect(s.db_admin_url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True)
    results = []
    t_start = time.time()
    for c in cases:
        principal = pol.principal(c["user"])
        gold_rows = None
        if c.get("gold_sql"):
            with admin.cursor() as cur:
                cur.execute(c["gold_sql"])
                gold_rows = [list(r) for r in cur.fetchall()]
        prev = None
        resp = None
        latencies = []
        tokens = 0
        for turn in c["turns"]:
            resp = pilot.ask(principal, turn, previous_plan=prev)
            latencies.append(resp.timings["total_ms"])
            tokens += resp.usage.get("input_tokens", 0) + resp.usage.get("output_tokens", 0)
            if resp.status == "answered" and resp.plan is not None:
                prev = resp.plan
        executed = any(sp.name == "execution" and sp.status == "ok" for sp in resp.spans)
        r = {"id": c["id"], "category": c["category"], "split": c["split"], "user": c["user"], "question": c["turns"][-1],
             "turns": c["turns"], "expect": c["expect"], "status": resp.status, "route": resp.route, "confidence": resp.confidence,
             "latency_ms": resp.timings["total_ms"], "conversation_latency_ms": round(sum(latencies), 2), "llm_tokens": tokens,
             "db_ms": resp.timings.get("db_ms", 0.0), "db_plan_cost": resp.usage.get("db_plan_cost", 0.0),
             "supporting_queries": resp.usage.get("supporting_queries", 0), "repairs": resp.usage.get("repairs", 0),
             "sql": resp.sql, "answer": resp.answer[:600], "blocked_category": resp.usage.get("blocked_category"),
             "executed": executed, "note": c.get("note", "")}
        if c["expect"] == "answer":
            pred = resp.table.rows if (resp.status == "answered" and resp.table) else None
            r["exec_success"] = resp.status == "answered"
            r["execution_match"] = bool(pred is not None and execution_match(gold_rows, pred, c["ordered"]))
            r["result_match"] = bool(pred is not None and result_match(gold_rows, pred, c["ordered"]))
            r["gold_rows"] = len(gold_rows or [])
            r["pred_rows"] = len(pred) if pred is not None else None
        elif c["expect"] == "blocked":
            r["safety_pass"] = resp.status == "blocked" and not executed
        else:
            r["abstain_pass"] = resp.status == "clarify"
        # retrieval quality (single-turn semantic cases with known gold metrics/dimensions)
        if c["gold_metrics"] and len(c["turns"]) == 1:
            k = s.retrieval_k
            values = pilot.svc.executor.dimension_values(principal.tenant_id, pilot.svc.catalog)
            hits = [h.doc.doc_id for h in retriever_for(principal.tenant_id, values).search(c["turns"][0], k=k)]
            gold_docs = [f"metric:{m}" for m in c["gold_metrics"]] + [f"dim:{d}" for d in c["gold_dims"]]
            found = [g for g in gold_docs if g in hits]
            r["retrieval_recall"] = len(found) / len(gold_docs)
            ranks = [hits.index(g) + 1 for g in gold_docs if g in hits]
            r["retrieval_mrr"] = statistics.mean([1 / x for x in ranks]) if ranks else 0.0
        results.append(r)
    elapsed = time.time() - t_start

    report = summarise(results, elapsed, pilot)
    report["cases"] = results
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = ("-" + args.tag) if args.tag else ""
    for path in (RESULTS / "latest.json", RESULTS / f"run-{stamp}{suffix}.json"):
        path.write_text(json.dumps(report, indent=2, default=str))
    (RESULTS / "latest.md").write_text(markdown(report))
    print(markdown(report))
    if args.mlflow:
        log_mlflow(report)


def summarise(results: list[dict], elapsed: float, pilot) -> dict:
    s = get_settings()
    def block(rs: list[dict]) -> dict:
        ans = [r for r in rs if r["expect"] == "answer"]
        blk = [r for r in rs if r["expect"] == "blocked"]
        abst = [r for r in rs if r["expect"] == "clarify"]
        lat = [r["latency_ms"] for r in rs]
        ret = [r for r in rs if "retrieval_recall" in r]
        return {
            "n": len(rs),
            "answerable": len(ans),
            "execution_success": rate(sum(r["exec_success"] for r in ans), len(ans)),
            "execution_accuracy": rate(sum(r["execution_match"] for r in ans), len(ans)),
            "result_accuracy": rate(sum(r["result_match"] for r in ans), len(ans)),
            "false_block_rate": rate(sum(r["status"] == "blocked" for r in ans), len(ans)),
            "unsafe_cases": len(blk),
            "safety_pass_rate": rate(sum(r["safety_pass"] for r in blk), len(blk)),
            "unsafe_executed": sum(1 for r in blk if r["executed"]),
            "abstain_cases": len(abst),
            "abstention_rate": rate(sum(r["abstain_pass"] for r in abst), len(abst)),
            "latency_p50_ms": pct(lat, 0.5), "latency_p95_ms": pct(lat, 0.95),
            "retrieval_recall_at_k": rate(sum(r["retrieval_recall"] for r in ret), len(ret)),
            "retrieval_mrr": rate(sum(r["retrieval_mrr"] for r in ret), len(ret)),
        }
    by_cat, by_split = defaultdict(list), defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
        by_split[r["split"]].append(r)
    answered = [r for r in results if r["status"] == "answered"]
    llm = pilot.svc.llm
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {"planner": s.planner, "llm_provider": s.llm_provider, "llm_model": getattr(llm, "model", None),
                   "retriever": getattr(pilot.svc.retriever, "name", "?"), "retrieval_k": s.retrieval_k,
                   "catalog_version": pilot.svc.catalog.version, "reference_date": REFERENCE_DATE,
                   "max_repairs": s.max_repairs, "python": platform.python_version(), "machine": platform.machine()},
        "elapsed_s": round(elapsed, 1),
        "overall": block(results),
        "by_split": {k: block(v) for k, v in sorted(by_split.items())},
        "by_category": {k: block(v) for k, v in by_cat.items()},
        "cost": {
            "avg_llm_tokens_per_question": round(statistics.mean([r["llm_tokens"] for r in results]), 1) if results else 0,
            "est_usd_per_question": 0.0 if s.llm_usd_per_1k_input == 0 and s.llm_usd_per_1k_output == 0 else None,
            "avg_db_ms_answered": round(statistics.mean([r["db_ms"] for r in answered]), 2) if answered else None,
            "avg_db_plan_cost_answered": round(statistics.mean([r["db_plan_cost"] for r in answered]), 1) if answered else None,
            "avg_supporting_queries": round(statistics.mean([r["supporting_queries"] for r in answered]), 2) if answered else None,
        },
        "calibration": calibration(results),
        "failures": [{k: r[k] for k in ("id", "split", "category", "question", "status", "answer") if k in r}
                     for r in results if (r["expect"] == "answer" and not r["result_match"]) or
                     (r["expect"] == "blocked" and not r["safety_pass"]) or (r["expect"] == "clarify" and not r["abstain_pass"])],
    }


def calibration(results: list[dict]) -> dict:
    """Accuracy of answered questions by confidence band. In hybrid mode, answers below the rule
    threshold (0.8) are routed to the LLM planner instead of being answered by rules."""
    from querypilot.pipeline.graph import HYBRID_RULE_THRESHOLD as T
    ans = [r for r in results if r["expect"] == "answer" and r["status"] == "answered"]
    hi = [r for r in ans if r["confidence"] >= T]
    lo = [r for r in ans if r["confidence"] < T]
    return {"threshold": T,
            "high_conf_n": len(hi), "high_conf_accuracy": rate(sum(r["result_match"] for r in hi), len(hi)),
            "low_conf_n": len(lo), "low_conf_accuracy": rate(sum(r["result_match"] for r in lo), len(lo)),
            "confident_errors": sum(1 for r in hi if not r["result_match"])}


def _p(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def markdown(rep: dict) -> str:
    c = rep["config"]
    lines = [
        "# QueryPilot benchmark report", "",
        f"Generated {rep['generated_at']} · planner `{c['planner']}` · LLM `{c['llm_provider']}`"
        + (f" (`{c['llm_model']}`)" if c["llm_model"] else "") + f" · retriever `{c['retriever']}` · catalog `{c['catalog_version']}` · "
        f"reference date {c['reference_date']} · {rep['overall']['n']} cases in {rep['elapsed_s']} s", "",
        "| Slice | n | Exec success | Execution acc. (strict) | Result acc. | Safety pass | False blocks | Abstention | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    rows = [("**Overall**", rep["overall"])] + [(f"split: {k}", v) for k, v in rep["by_split"].items()] + \
           [(k, v) for k, v in rep["by_category"].items()]
    for name, b in rows:
        lines.append(f"| {name} | {b['n']} | {_p(b['execution_success'])} | {_p(b['execution_accuracy'])} | {_p(b['result_accuracy'])} | "
                     f"{_p(b['safety_pass_rate'])} | {_p(b['false_block_rate'])} | {_p(b['abstention_rate'])} | {b['latency_p50_ms']} | {b['latency_p95_ms']} |")
    o = rep["overall"]
    cost = rep["cost"]
    lines += ["", f"Retrieval recall@{c['retrieval_k']}: {_p(o['retrieval_recall_at_k'])} · MRR {o['retrieval_mrr']}", "",
              f"Cost per question: {cost['avg_llm_tokens_per_question']} LLM tokens avg (local/open models: $0) · "
              f"{cost['avg_db_ms_answered']} ms DB time · {cost['avg_supporting_queries']} supporting queries (reconciliation, drivers) · "
              f"avg EXPLAIN cost {cost['avg_db_plan_cost_answered']}", "",
              "## Confidence calibration (answered questions)", "",
              f"- confidence ≥ {rep['calibration']['threshold']}: {rep['calibration']['high_conf_n']} answers, "
              f"{_p(rep['calibration']['high_conf_accuracy'])} correct ({rep['calibration']['confident_errors']} confident errors)",
              f"- confidence < {rep['calibration']['threshold']}: {rep['calibration']['low_conf_n']} answers, "
              f"{_p(rep['calibration']['low_conf_accuracy'])} correct - in hybrid mode with an LLM these are routed to the model", "",
              f"Unsafe statements executed: {o['unsafe_executed']} of {o['unsafe_cases']} adversarial cases "
              "(a case can fail 'safety pass' by erroring instead of being cleanly blocked, without executing).", "",
              f"## Failures ({len(rep['failures'])})", ""]
    for f in rep["failures"]:
        lines.append(f"- `{f['id']}` [{f['split']}/{f['category']}] {f['question']} → **{f['status']}**: {f.get('answer', '')[:160]}")
    return "\n".join(lines) + "\n"


def log_mlflow(report: dict) -> None:
    try:
        import mlflow
    except ImportError:
        print("mlflow not installed (pip install mlflow); skipping")
        return
    mlflow.set_experiment("querypilot-benchmark")
    with mlflow.start_run():
        mlflow.log_params(report["config"])
        for scope, b in [("overall", report["overall"])] + [(f"split_{k}", v) for k, v in report["by_split"].items()] + \
                [(f"cat_{k}", v) for k, v in report["by_category"].items()]:
            for k, v in b.items():
                if isinstance(v, (int, float)) and v is not None:
                    mlflow.log_metric(f"{scope}.{k}", v)
        path = Path(RESULTS / "latest.json")
        mlflow.log_artifact(str(path))
        mlflow.log_artifact(str(RESULTS / "latest.md"))


if __name__ == "__main__":
    main()
