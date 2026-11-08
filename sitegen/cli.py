"""Command line entry point: `uv run cpp-guide {build,serve,check,new-problem}`."""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

from . import builder, judge, server
from .content import PRACTICE_DIR, load_problems


def cmd_check(args: argparse.Namespace) -> int:
    """Compile every problem's reference solution against its tests (and check starters compile)."""
    problems = [p for p in load_problems() if not args.only or p.slug in args.only]

    def check(p):
        issues = []
        if not p.tests:
            issues.append("missing Tests")
        if not p.solution:
            issues.append("missing Solution code")
        if issues:
            return p, issues
        r = judge.run(p.solution, p.tests)
        if not r["ok"]:
            issues.append(f"solution fails ({r['stage']}):\n{r['output']}")
        if p.starter:
            s = judge.run(p.starter, p.tests)
            if s["stage"] == "compile":
                issues.append(f"starter code does not compile:\n{s['output']}")
        return p, issues

    failed = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for p, issues in pool.map(check, problems):
            status = "ok  " if not issues else "FAIL"
            print(f"[{status}] #{p.num:03d} {p.slug}")
            for issue in issues:
                failed += 1
                print("       " + issue.replace("\n", "\n       "))
    print(f"\n{len(problems)} problems checked, {failed} issue(s)")
    return 1 if failed else 0


TEMPLATE = """+++
title = "{title}"
difficulty = "easy"   # easy | medium | hard
tags = ["stl"]
+++

Describe the problem here.

## Starter Code

```cpp
#pragma once

```

## Hints

- First hint.

## Solution

```cpp
#pragma once

```

Explain the solution.

## Tests

```cpp
int main() {{
    CHECK(true);
    return judge::report();
}}
```
"""


def cmd_new(args: argparse.Namespace) -> int:
    nums = [int(p.name.split("-", 1)[0]) for p in PRACTICE_DIR.glob("*.md")]
    num = max(nums, default=0) + 1
    slug = args.slug.strip().lower().replace(" ", "-")
    path = PRACTICE_DIR / f"{num:03d}-{slug}.md"
    path.write_text(TEMPLATE.format(title=slug.replace("-", " ").title()))
    print(f"created {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cpp-guide", description="Build and serve the cpp-guide textbook.")
    sub = ap.add_subparsers(dest="cmd")

    b = sub.add_parser("build", help="build the static site into site/")
    b.add_argument("--base", default="", help="URL prefix when hosting under a sub-path, e.g. /cpp-guide")

    s = sub.add_parser("serve", help="build, serve, rebuild on change, and run practice submissions")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--no-watch", action="store_true")
    s.add_argument("--no-build", action="store_true", help="serve the existing site/ without rebuilding")
    s.add_argument("--runner-url", default=None, help="forward /api/run to this runner (e.g. http://runner:9000)")
    s.add_argument("--no-runner", action="store_true", help="disable /api/run entirely")

    rn = sub.add_parser("runner", help="run only the code runner (POST /api/run), for an isolated container")
    rn.add_argument("--host", default="0.0.0.0")
    rn.add_argument("--port", type=int, default=9000)

    c = sub.add_parser("check", help="verify every practice problem's reference solution passes its tests")
    c.add_argument("only", nargs="*", help="problem slugs to check (default: all)")
    c.add_argument("-j", "--jobs", type=int, default=4)

    n = sub.add_parser("new-problem", help="scaffold a new practice problem")
    n.add_argument("slug")

    args = ap.parse_args(argv)
    if args.cmd == "build":
        builder.build(base=args.base)
        return 0
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "new-problem":
        return cmd_new(args)
    if args.cmd == "runner":
        server.run_runner(args.host, args.port)
        return 0
    if args.cmd in (None, "serve"):
        server.serve(getattr(args, "host", "127.0.0.1"), getattr(args, "port", 8000),
                     watch=not getattr(args, "no_watch", False), build=not getattr(args, "no_build", False),
                     runner_url=getattr(args, "runner_url", None), runner=not getattr(args, "no_runner", False))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
