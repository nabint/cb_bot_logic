from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from rag_cb.indexer import DEFAULT_MODEL_NAME
    from rag_cb.limit_context import get_limited_context
    from rag_cb.test_file import resolve_diff_text
else:
    from .indexer import DEFAULT_MODEL_NAME
    from .limit_context import get_limited_context
    from .test_file import resolve_diff_text


HARD_CODED_DIFF = """PR Info:

Previous title: 'Swap return values in get_winning_card function'

Branch: 'changed_order'

Commit messages:
=====
1. Changed the context
=====


The PR Git Diff:
=====
## File: 'callbreak_mcts/utils.py'

@@ -60,27 +60,27 @@ def get_winning_card(played_cards):
     best_rank = int(played_cards[0][:-1])
     best_suit = led_suit
     best_idx = 0
 
     for i, card in enumerate(played_cards[1:], 1):
         card_suit = SUIT_MAP[card[-1]]
         card_rank = int(card[:-1])
 
         if card_suit == Suit.SPADES and best_suit != Suit.SPADES:
             best_card, best_rank, best_suit, best_idx = card, card_rank, card_suit, i
         elif card_suit == best_suit and card_rank > best_rank:
             best_card, best_rank, best_idx = card, card_rank, i
 
-    return best_card, best_idx
+    return  best_idx, best_card
 
 
 # -------------------------------------------------------------------------
 # Track void suits from trick play
 # -------------------------------------------------------------------------
 def track_void_suits(trick_cards, first_player_idx, void_tracker):
     \"""
     Track which suits each player is void in based on trick play.
     If a player plays off-suit from the led suit, they're void in it.
     void_tracker is a list of 4 lists (or sets).
     \"""
     if not trick_cards:
         return
=====
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Return context trimmed to a token budget using a passed diff or the tracked repo diff."
    )
    parser.add_argument("--repo", default=".", help="Repository root to inspect.")
    parser.add_argument("--db", default=None, help="Optional sqlite index path.")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL_NAME, help="SentenceTransformer model name."
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=8,
        help="Number of semantic matches to return before trimming.",
    )
    parser.add_argument(
        "--per-file-top-k",
        type=int,
        default=2,
        help="Number of same-file semantic matches to return before trimming.",
    )
    parser.add_argument(
        "--token-budget",
        type=int,
        required=True,
        help="Maximum total context tokens to return.",
    )
    parser.add_argument(
        "--diff-file",
        default=None,
        help="Optional path to a git diff file. Reads stdin when piped; falls back to tracked diff otherwise.",
    )
    parser.add_argument(
        "--auto-build",
        action="store_true",
        help="Build the index automatically if it does not exist.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    diff_text = resolve_diff_text(repo_path=args.repo, diff_file=args.diff_file)
    if not diff_text.strip():
        diff_text = HARD_CODED_DIFF

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

    print(
        f"Estimated tokens: {limited_context.estimated_tokens}/{limited_context.token_budget}"
    )
    print(limited_context.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
