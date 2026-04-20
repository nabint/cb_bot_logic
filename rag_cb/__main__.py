from __future__ import annotations

import argparse
import sys

from .context import get_context
from .indexer import DEFAULT_MODEL_NAME, index_repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Code indexing and diff-context retrieval for this repo.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Build or refresh the repository code index.")
    index_parser.add_argument("--repo", default=".", help="Repository root to index.")
    index_parser.add_argument("--db", default=None, help="Optional sqlite database path.")
    index_parser.add_argument("--model", default=DEFAULT_MODEL_NAME, help="SentenceTransformer model name.")
    index_parser.add_argument("--chunk-size-lines", type=int, default=80, help="Maximum lines per chunk.")
    index_parser.add_argument("--chunk-overlap-lines", type=int, default=20, help="Overlap between chunks.")

    context_parser = subparsers.add_parser("context", help="Resolve relevant code context from a git diff.")
    context_parser.add_argument("--repo", default=".", help="Repository root containing the index.")
    context_parser.add_argument("--db", default=None, help="Optional sqlite database path.")
    context_parser.add_argument("--model", default=DEFAULT_MODEL_NAME, help="SentenceTransformer model name.")
    context_parser.add_argument("--top-k", type=int, default=8, help="Number of cross-repo semantic matches to return.")
    context_parser.add_argument("--per-file-top-k", type=int, default=2, help="Number of same-file semantic matches to return.")
    context_parser.add_argument("--diff-file", default=None, help="Optional path to a git diff file. Reads stdin when omitted.")
    context_parser.add_argument("--auto-build", action="store_true", help="Build the index automatically if it does not exist.")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "index":
        stats = index_repository(
            args.repo,
            db_path=args.db,
            model_name=args.model,
            chunk_size_lines=args.chunk_size_lines,
            chunk_overlap_lines=args.chunk_overlap_lines,
        )
        print(
            "Indexed "
            f"{stats.files_indexed} files into {stats.db_path} "
            f"({stats.chunks_indexed} chunks, {stats.files_skipped} skipped, {stats.files_removed} removed)."
        )
        return 0

    if args.command == "context":
        if args.diff_file:
            with open(args.diff_file, "r", encoding="utf-8") as diff_handle:
                diff_text = diff_handle.read()
        else:
            diff_text = sys.stdin.read()

        if not diff_text.strip():
            parser.error("No git diff content provided. Pass --diff-file or pipe a diff into stdin.")

        context = get_context(
            diff_text,
            args.repo,
            db_path=args.db,
            model_name=args.model,
            top_k=args.top_k,
            per_file_top_k=args.per_file_top_k,
            auto_build=args.auto_build,
        )
        print(context.render())
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
