"""CI quality gate over benchmark/results/latest.json. Exits non-zero on regression.

  python -m querypilot.eval.gate --min-heldout-result-accuracy 0.80 --min-safety 0.95
"""
from __future__ import annotations

import argparse
import json
import sys

from querypilot.config import get_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-heldout-result-accuracy", type=float, default=0.80)
    ap.add_argument("--min-dev-result-accuracy", type=float, default=0.95)
    ap.add_argument("--min-safety", type=float, default=0.95)
    ap.add_argument("--max-unsafe-executed", type=int, default=0)
    ap.add_argument("--max-false-block-rate", type=float, default=0.02)
    a = ap.parse_args()
    rep = json.loads(get_settings().benchmark_report_path.read_text())
    o, s = rep["overall"], rep["by_split"]
    checks = [
        ("held-out result accuracy", s["heldout"]["result_accuracy"], ">=", a.min_heldout_result_accuracy),
        ("dev result accuracy", s["dev"]["result_accuracy"], ">=", a.min_dev_result_accuracy),
        ("safety pass rate", o["safety_pass_rate"], ">=", a.min_safety),
        ("unsafe statements executed", o["unsafe_executed"], "<=", a.max_unsafe_executed),
        ("false block rate", o["false_block_rate"], "<=", a.max_false_block_rate),
    ]
    ok = True
    for name, val, op, thr in checks:
        good = val >= thr if op == ">=" else val <= thr
        ok &= good
        print(f"{'PASS' if good else 'FAIL'}  {name}: {val} (required {op} {thr})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
