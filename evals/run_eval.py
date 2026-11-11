"""Yürütme doğruluğu (execution accuracy) ölçümü.

Her soru için elle yazılmış "doğru" SQL ile Kâşif'in ürettiği SQL aynı veritabanında
çalıştırılır ve sonuç kümeleri karşılaştırılır (satır sırası ve sütun adları
önemsizdir, sayılar yuvarlanır). Cevabı veride olmayan sorular için beklenen
davranış, tahmin yürütmeden reddetmektir.

Kullanım:
    python evals/run_eval.py              # kural motoru (LLM gerekmez)
    python evals/run_eval.py --mode llm   # LLM modu (Ollama vb. gerekir)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kasif.config import Settings  # noqa: E402
from kasif.engine import Engine  # noqa: E402

TODAY = date(2026, 11, 10)


def norm(rows: list) -> Counter:
    def cell(v):
        if isinstance(v, float):
            return round(v, 1)
        return v

    return Counter(tuple(sorted((cell(v) for v in r), key=lambda x: (str(type(x)), str(x)))) for r in rows)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "rules", "llm"], default="auto")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--set", default="questions", help="questions (geliştirme) ya da holdout (saklı)")
    args = ap.parse_args(argv)
    items = [
        json.loads(line) for line in (ROOT / "evals" / f"{args.set}.jsonl").read_text("utf-8").splitlines() if line
    ]
    # ignore_cleanup_errors: Windows can briefly keep SQLite files locked after use.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        base = "http://127.0.0.1:11434/v1" if args.mode == "llm" else "http://127.0.0.1:9/v1"
        e = Engine(Settings(Path(tmp), base, "ollama", "qwen2.5-coder:7b"), today=TODAY)
        e.registry.ensure_demo(TODAY)
        db = sqlite3.connect(e.path())
        try:
            answerable = [i for i in items if i["gold_sql"]]
            unanswerable = [i for i in items if not i["gold_sql"]]
            answered = correct = 0
            for it in answerable:
                ans = e.ask(it["question"], args.mode, "tr")
                gold = db.execute(it["gold_sql"]).fetchall()
                ok = ans.ok and norm(ans.rows) == norm(gold)
                answered += ans.ok
                correct += ok
                if args.verbose or not ok:
                    print(f"{'✓' if ok else '✗'} {it['question']}" + ("" if ans.ok else f"  [{ans.error_code}]"))
            refused = 0
            for it in unanswerable:
                ans = e.ask(it["question"], args.mode, "tr")
                refused += not ans.ok
                if args.verbose or ans.ok:
                    print(f"{'✓' if not ans.ok else '✗'} (cevapsız) {it['question']}")
        finally:
            db.close()  # close before the temp folder is removed (required on Windows)
    n = len(answerable)
    print(f"\nSet: {args.set}  ·  mod: {args.mode}  ·  bugün: {TODAY}")
    print(f"Cevaplanan           {answered}/{n}  (%{100 * answered / n:.0f})")
    print(f"Yürütme doğruluğu    {correct}/{n}  (%{100 * correct / n:.0f})")
    print(f"Doğru reddedilen     {refused}/{len(unanswerable)}")
    return 0 if correct == n and refused == len(unanswerable) else 1


if __name__ == "__main__":
    sys.exit(main())
