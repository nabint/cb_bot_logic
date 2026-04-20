from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from rag_cb.indexer import DEFAULT_MODEL_NAME
    from rag_cb.limit_context import get_limited_context
    from rag_cb.test_file import get_tracked_repo_diff
else:
    from .indexer import DEFAULT_MODEL_NAME
    from .limit_context import get_limited_context
    from .test_file import get_tracked_repo_diff


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read tracked git diff from this repo and return context trimmed to a token budget."
    )
    parser.add_argument("--repo", default=".", help="Repository root to inspect.")
    parser.add_argument("--db", default=None, help="Optional sqlite index path.")
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME, help="SentenceTransformer model name.")
    parser.add_argument("--top-k", type=int, default=8, help="Number of semantic matches to return before trimming.")
    parser.add_argument("--per-file-top-k", type=int, default=2, help="Number of same-file semantic matches to return before trimming.")
    parser.add_argument("--token-budget", type=int, required=True, help="Maximum total context tokens to return.")
    parser.add_argument(
        "--auto-build",
        action="store_true",
        help="Build the index automatically if it does not exist.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    diff_text = get_tracked_repo_diff(args.repo)
    if not diff_text.strip():
        print("No tracked changes found outside rag_cb.")
        return 0

    limited_context = get_limited_context(
        diff_text,
        args.repo,
        token_budget=args.token_budget,
        db_path=args.db,
        model_name=args.model,
        top_k=args.top_k,
        per_file_top_k=args.per_file_top_k,
        auto_build=args.auto_build,
    )

    print(f"Estimated tokens: {limited_context.estimated_tokens}/{limited_context.token_budget}")
    print(limited_context.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
