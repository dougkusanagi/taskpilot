"""CLI: N1 run, fixtures serve, episódio fino em simulação."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from evals.brain.client import Client
from evals.brain.fixtures import Fixture, serve
from evals.brain.loop import run_episode
from evals.brain.runner import run, save
from evals.brain.verification import verify_episode


def main():
    parser = argparse.ArgumentParser(description="Bancada local TaskPilot N1")
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--model", required=True)
    runner.add_argument("--endpoint", default="http://127.0.0.1:8091")
    runner.add_argument("--server-pid", type=int)
    runner.add_argument("--repetitions", type=int, default=3)
    runner.add_argument("--split", choices=["all", "dev", "blind"], default="all")
    runner.add_argument(
        "--mode",
        choices=["matrix", "free-off", "schema-off", "free-on", "schema-on"],
        default="matrix",
    )
    runner.add_argument("--max-tokens", type=int, default=512)
    runner.add_argument("--output", type=Path)
    runner.add_argument("--runtime-manifest", type=Path)
    fixtures = sub.add_parser("serve")
    fixtures.add_argument("--port", type=int, default=8765)
    episode = sub.add_parser("episode")
    episode.add_argument("--model", required=True)
    episode.add_argument("--endpoint", default="http://127.0.0.1:8091")
    episode.add_argument(
        "--fixture",
        choices=["loja", "webmail", "noticias", "injecao", "senha", "captcha"],
        required=True,
    )
    episode.add_argument("--goal", required=True)
    episode.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "serve":
        serve(args.port)
    elif args.command == "run":
        report = run(
            args.model,
            args.endpoint,
            pid=args.server_pid,
            repetitions=args.repetitions,
            split=args.split,
            mode=args.mode,
            max_tokens=args.max_tokens,
            output=args.output,
            runtime_manifest=args.runtime_manifest,
        )
        print(json.dumps([c["summary"] for c in report["conditions"]], indent=2))
    else:
        env = Fixture(args.fixture)
        client = Client(args.endpoint, args.model, constrained=True, thinking=False)
        try:
            client.check_model()
            result = run_episode(args.goal, env, lambda p: client.complete(p).content)
            save(
                args.output,
                {"episode": asdict(result), "verification": verify_episode(env, result)},
            )
        finally:
            client.close()


if __name__ == "__main__":
    main()
