from __future__ import annotations

import argparse
import json
from pathlib import Path

from .budget import BudgetLedger
from .commands import command_index, parse_command
from .context import estimate_tokens
from .credentials import CredentialBroker
from .executor import RunnerRuntime, current_status, execute
from .instructions import InstructionStore
from .paths import ensure_runtime_dirs, state_root, workspace_root
from .providers import provider_status


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(prog="ai-remote-runner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    parse_p = sub.add_parser("parse")
    parse_p.add_argument("raw_text")
    exec_p = sub.add_parser("execute")
    exec_p.add_argument("raw_text")
    sub.add_parser("index")
    sub.add_parser("providers")
    budget_p = sub.add_parser("budget")
    budget_p.add_argument("--reserve-run")
    budget_p.add_argument("--provider", default="claude-code")
    budget_p.add_argument("--usd", type=float, default=0.1)
    instr_p = sub.add_parser("instruction")
    instr_p.add_argument("scope", choices=["global", "project"])
    instr_p.add_argument("action", choices=["show", "set", "append"])
    instr_p.add_argument("--workspace", default="default")
    instr_p.add_argument("--text", default="")
    cred_p = sub.add_parser("credential-list")
    cred_p.add_argument("--root")
    cred_add = sub.add_parser("credential-add-secret")
    cred_add.add_argument("--metadata-json", required=True)
    cred_add.add_argument("--root")
    ctx_p = sub.add_parser("estimate-context")
    ctx_p.add_argument("text", nargs="*")
    bridge_p = sub.add_parser("bridge")
    bridge_p.add_argument("--host", default="127.0.0.1")
    bridge_p.add_argument("--port", type=int, default=8765)
    sub.add_parser("telegram")
    args = parser.parse_args()

    ensure_runtime_dirs()
    if args.cmd == "status":
        print_json(current_status(RunnerRuntime.default()))
    elif args.cmd == "parse":
        print_json(parse_command(args.raw_text, allow_bare=False))
    elif args.cmd == "execute":
        parsed = parse_command(args.raw_text, allow_bare=False)
        print_json(execute(parsed, {"raw_text": args.raw_text}))
    elif args.cmd == "index":
        print_json(command_index())
    elif args.cmd == "providers":
        print_json(provider_status())
    elif args.cmd == "budget":
        ledger = BudgetLedger(state_root() / "budget" / "ledger.json")
        if args.reserve_run:
            print_json(ledger.reserve(args.reserve_run, args.provider, args.usd))
        else:
            print_json(ledger.load())
    elif args.cmd == "instruction":
        store = InstructionStore(state_root() / "instructions" / "global.md", workspace_root())
        if args.action == "show":
            print_json(store.show(args.scope, args.workspace))
        else:
            print_json(store.write(args.scope, args.text, args.workspace, append=args.action == "append"))
    elif args.cmd == "credential-list":
        broker = CredentialBroker(Path(args.root) if args.root else state_root() / "credentials")
        print_json(broker.list_public())
    elif args.cmd == "credential-add-secret":
        import sys

        broker = CredentialBroker(Path(args.root) if args.root else state_root() / "credentials")
        metadata = json.loads(args.metadata_json)
        print_json(broker.add_local_secret(metadata, sys.stdin.read()))
    elif args.cmd == "estimate-context":
        print_json({"estimated_tokens": estimate_tokens(*args.text)})
    elif args.cmd == "bridge":
        from .bridge import serve

        serve(args.host, args.port)
    elif args.cmd == "telegram":
        from .telegram import serve

        serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
