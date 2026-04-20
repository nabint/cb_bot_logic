from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from rag_cb.context import get_context
    from rag_cb.indexer import DEFAULT_MODEL_NAME
else:
    from .context import get_context
    from .indexer import DEFAULT_MODEL_NAME


def get_tracked_repo_diff(repo_path: str | Path = ".") -> str:
    repo_root = Path(repo_path).resolve()

    base_command = [
        "git",
        "-C",
        str(repo_root),
        "diff",
        "HEAD",
        "--",
        ".",
        ":(exclude)rag_cb",
        ":(exclude).rag_cb_index",
    ]

    result = subprocess.run(
        base_command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout

    has_head = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if has_head.returncode == 0:
        raise RuntimeError(result.stderr.strip() or "git diff HEAD failed")

    unstaged = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "diff",
            "--",
            ".",
            ":(exclude)rag_cb",
            ":(exclude).rag_cb_index",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    staged = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "diff",
            "--cached",
            "--",
            ".",
            ":(exclude)rag_cb",
            ":(exclude).rag_cb_index",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    if unstaged.returncode != 0:
        raise RuntimeError(unstaged.stderr.strip() or "git diff failed")
    if staged.returncode != 0:
        raise RuntimeError(staged.stderr.strip() or "git diff --cached failed")

    pieces = [piece for piece in (staged.stdout, unstaged.stdout) if piece.strip()]
    return "\n".join(pieces)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read tracked git diff from this repo and fetch relevant context from the rag_cb index."
    )
    parser.add_argument("--repo", default=".", help="Repository root to inspect.")
    parser.add_argument("--db", default=None, help="Optional sqlite index path.")
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME, help="SentenceTransformer model name.")
    parser.add_argument("--top-k", type=int, default=8, help="Number of semantic matches to return.")
    parser.add_argument("--per-file-top-k", type=int, default=2, help="Number of same-file semantic matches to return.")
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

    context = get_context(
        diff_text,
        args.repo,
        db_path=args.db,
        model_name=args.model,
        top_k=args.top_k,
        per_file_top_k=args.per_file_top_k,
        auto_build=args.auto_build,
    )

    print(len(context.render()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
