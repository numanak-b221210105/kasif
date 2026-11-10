"""Komut satırı: kasif ask "Bu yıl ciro ne kadar?" | kasif sql "SELECT ..." | kasif demo"""

from __future__ import annotations

import argparse
import sys

from .config import settings
from .engine import Engine
from .insights import fmt_value


def _print(ans) -> int:
    if not ans.ok:
        print(f"✗ {ans.error}")
        for s in ans.suggestions[:4]:
            print(f"  · {s}")
        return 1
    for p in ans.plan:
        print(f"  {p.label:<10} {p.text}")
    print("\n" + (ans.sql or "") + "\n")
    names = [c.name for c in ans.columns]
    widths = [
        max(len(n), *(len(fmt_value(r[i], ans.columns[i].fmt, compact=False)) for r in ans.rows[:20]))
        for i, n in enumerate(names)
    ]
    print("  ".join(n.ljust(w) for n, w in zip(names, widths, strict=True)))
    for r in ans.rows[:20]:
        print("  ".join(fmt_value(v, ans.columns[i].fmt, compact=False).ljust(widths[i]) for i, v in enumerate(r)))
    if ans.row_count > 20:
        print(f"… {ans.row_count - 20} satır daha")
    print()
    for s in ans.summary:
        print("• " + s.replace("**", ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="kasif", description="Türkçe sorularla veritabanı analizi")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="soru sor")
    a.add_argument("question")
    a.add_argument("--mode", choices=["auto", "rules", "llm"], default="auto")
    q = sub.add_parser("sql", help="SQL çalıştır (salt-okunur)")
    q.add_argument("sql")
    sub.add_parser("examples", help="örnek sorular")
    args = p.parse_args(argv)
    e = Engine(settings())
    if args.cmd == "ask":
        return _print(e.ask(args.question, args.mode))
    if args.cmd == "sql":
        return _print(e.run_sql(args.sql))
    for x in e.examples():
        print(x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
