from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Sequence

from .context import ContextBundle, ContextMatch, ImpactAnchor, get_context

TOKEN_PIECE_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
IMPORTANT_LINE_RE = re.compile(r"^\s*(?:def|class|func|return|yield|raise|from|import)\b")
_ENCODER_STATE: tuple[object | None, bool] = (None, False)


@dataclass(slots=True)
class LimitedContextMatch:
    path: str
    start_line: int
    end_line: int
    score: float
    reason: str
    content: str
    estimated_tokens: int
    truncated: bool = False


@dataclass(slots=True)
class LimitedContextBundle:
    changed_files: list[str]
    anchors: list[ImpactAnchor] = field(default_factory=list)
    matches: list[LimitedContextMatch] = field(default_factory=list)
    token_budget: int = 0
    estimated_tokens: int = 0
    omitted_matches: int = 0

    @property
    def context_text(self) -> str:
        return self.render()

    def render(self, *, max_chars: int | None = None) -> str:
        sections: list[str] = []

        if self.anchors:
            anchor_lines = [
                f"- {anchor.path}:{anchor.start_line}-{anchor.end_line} [{anchor.kind}] {anchor.qualified_name}"
                for anchor in self.anchors
            ]
            sections.append("## Impact Anchors\n" + "\n".join(anchor_lines))

        if self.matches:
            blocks: list[str] = []
            for match in self.matches:
                suffix = " truncated" if match.truncated else ""
                header = (
                    f"# {match.path}:{match.start_line}-{match.end_line} "
                    f"[{match.reason} score={match.score:.4f}{suffix}]"
                )
                blocks.append(f"{header}\n{match.content}".rstrip())
            sections.append("\n\n".join(blocks))

        rendered = "\n\n".join(section for section in sections if section)
        if max_chars is not None and len(rendered) > max_chars:
            return rendered[:max_chars].rstrip() + "\n..."
        return rendered

    def to_dict(self) -> dict[str, object]:
        return {
            "changed_files": list(self.changed_files),
            "token_budget": self.token_budget,
            "estimated_tokens": self.estimated_tokens,
            "omitted_matches": self.omitted_matches,
            "anchors": [
                {
                    "path": anchor.path,
                    "name": anchor.name,
                    "qualified_name": anchor.qualified_name,
                    "kind": anchor.kind,
                    "start_line": anchor.start_line,
                    "end_line": anchor.end_line,
                }
                for anchor in self.anchors
            ],
            "matches": [
                {
                    "path": match.path,
                    "start_line": match.start_line,
                    "end_line": match.end_line,
                    "score": match.score,
                    "reason": match.reason,
                    "estimated_tokens": match.estimated_tokens,
                    "truncated": match.truncated,
                    "content": match.content,
                }
                for match in self.matches
            ],
            "context_text": self.context_text,
        }


def get_limited_context(
    git_diff: str,
    repo_path: str = ".",
    *,
    token_budget: int,
    db_path: str | None = None,
    model_name: str | None = None,
    top_k: int = 8,
    per_file_top_k: int = 2,
    auto_build: bool = False,
) -> LimitedContextBundle:
    context = get_context(
        git_diff,
        repo_path,
        db_path=db_path,
        model_name=model_name or "sentence-transformers/all-MiniLM-L6-v2",
        top_k=top_k,
        per_file_top_k=per_file_top_k,
        auto_build=auto_build,
    )
    return limit_context_bundle(context, token_budget=token_budget)


def limit_context_bundle(
    context: ContextBundle,
    *,
    token_budget: int,
    max_anchor_tokens: int | None = None,
) -> LimitedContextBundle:
    if token_budget <= 0:
        raise ValueError("token_budget must be greater than zero")

    selected_anchors, anchor_tokens = _select_anchor_summary(
        context.anchors,
        token_budget=token_budget,
        max_anchor_tokens=max_anchor_tokens,
    )
    remaining_budget = max(0, token_budget - anchor_tokens)

    selected_matches: list[LimitedContextMatch] = []
    anchor_terms = _anchor_terms(context.anchors)

    for match in _budget_match_order(context.matches):
        if remaining_budget <= 0:
            break

        full_match = _full_limited_match(match)
        if full_match.estimated_tokens <= remaining_budget:
            selected_matches.append(full_match)
            remaining_budget -= full_match.estimated_tokens
            continue

        compact_match = _compact_match(match, remaining_budget, anchor_terms=anchor_terms)
        if compact_match is None:
            continue

        selected_matches.append(compact_match)
        remaining_budget -= compact_match.estimated_tokens

    rendered = _render_limited_bundle(selected_anchors, selected_matches)
    omitted_matches = max(0, len(context.matches) - len(selected_matches))
    return LimitedContextBundle(
        changed_files=context.changed_files[:],
        anchors=selected_anchors,
        matches=selected_matches,
        token_budget=token_budget,
        estimated_tokens=estimate_tokens(rendered),
        omitted_matches=omitted_matches,
    )


def estimate_tokens(text: str) -> int:
    if not text:
        return 0

    encoder = _get_encoder()
    if encoder is not None:
        return len(encoder.encode(text))

    piece_count = len(TOKEN_PIECE_RE.findall(text))
    char_estimate = math.ceil(len(text) / 4)
    return max(1, max(piece_count, char_estimate))


def _get_encoder():
    global _ENCODER_STATE
    encoder, attempted = _ENCODER_STATE
    if attempted:
        return encoder

    try:
        import tiktoken
    except ImportError:
        _ENCODER_STATE = (None, True)
        return None

    try:
        encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:
        encoder = None

    _ENCODER_STATE = (encoder, True)
    return encoder


def _select_anchor_summary(
    anchors: Sequence[ImpactAnchor],
    *,
    token_budget: int,
    max_anchor_tokens: int | None,
) -> tuple[list[ImpactAnchor], int]:
    if not anchors:
        return [], 0

    token_cap = max_anchor_tokens
    if token_cap is None:
        token_cap = min(max(40, token_budget // 8), 160)

    selected_anchors: list[ImpactAnchor] = []
    current_tokens = 0

    for anchor in anchors:
        next_lines = [
            f"- {candidate.path}:{candidate.start_line}-{candidate.end_line} [{candidate.kind}] {candidate.qualified_name}"
            for candidate in [*selected_anchors, anchor]
        ]
        block = "## Impact Anchors\n" + "\n".join(next_lines)
        block_tokens = estimate_tokens(block)
        if block_tokens > token_cap:
            break
        selected_anchors.append(anchor)
        current_tokens = block_tokens

    return selected_anchors, current_tokens


def _full_limited_match(match: ContextMatch) -> LimitedContextMatch:
    block = _render_match_block(match, match.content, truncated=False)
    return LimitedContextMatch(
        path=match.path,
        start_line=match.start_line,
        end_line=match.end_line,
        score=match.score,
        reason=match.reason,
        content=match.content,
        estimated_tokens=estimate_tokens(block),
        truncated=False,
    )


def _compact_match(
    match: ContextMatch,
    token_budget: int,
    *,
    anchor_terms: set[str],
) -> LimitedContextMatch | None:
    header = _render_match_header(match, truncated=True)
    header_tokens = estimate_tokens(header)
    if token_budget <= header_tokens:
        return None

    lines = match.content.splitlines()
    if not lines:
        return None

    selected_indexes: set[int] = set()
    candidate_order = _candidate_line_order(lines, anchor_terms)

    for index in candidate_order:
        trial_indexes = set(selected_indexes)
        trial_indexes.add(index)
        rendered_content = _render_selected_lines(lines, trial_indexes)
        block = f"{header}\n{rendered_content}".rstrip()
        if estimate_tokens(block) <= token_budget:
            selected_indexes = trial_indexes

    if not selected_indexes:
        content = _truncate_line_to_budget(lines[0], token_budget - header_tokens)
        if not content:
            return None
    else:
        content = _render_selected_lines(lines, selected_indexes)

    block = f"{header}\n{content}".rstrip()
    return LimitedContextMatch(
        path=match.path,
        start_line=match.start_line,
        end_line=match.end_line,
        score=match.score,
        reason=match.reason,
        content=content,
        estimated_tokens=estimate_tokens(block),
        truncated=True,
    )


def _candidate_line_order(lines: Sequence[str], anchor_terms: set[str]) -> list[int]:
    weighted_indexes: list[tuple[int, int]] = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        score = 0
        if index == 0:
            score += 120
        if index == len(lines) - 1:
            score += 15
        if IMPORTANT_LINE_RE.match(stripped):
            score += 70
        if anchor_terms and any(term in line for term in anchor_terms):
            score += 90
        if stripped.startswith(("if ", "elif ", "else", "for ", "while ", "try:", "except ", "with ")):
            score += 20
        if stripped:
            score += 5
        weighted_indexes.append((-score, index))

    ordered: list[int] = []
    seen: set[int] = set()
    for _, index in sorted(weighted_indexes):
        for neighbor in (index - 1, index, index + 1):
            if 0 <= neighbor < len(lines) and neighbor not in seen:
                seen.add(neighbor)
                ordered.append(neighbor)

    for index in range(len(lines)):
        if index not in seen:
            ordered.append(index)
            seen.add(index)

    return ordered


def _render_selected_lines(lines: Sequence[str], selected_indexes: set[int]) -> str:
    if not selected_indexes:
        return ""

    ordered_indexes = sorted(selected_indexes)
    blocks: list[str] = []
    current_block: list[str] = []
    previous_index: int | None = None

    for index in ordered_indexes:
        if previous_index is not None and index != previous_index + 1:
            if current_block:
                blocks.append("\n".join(current_block).rstrip())
                current_block = []
            blocks.append("...")

        current_block.append(lines[index])
        previous_index = index

    if current_block:
        blocks.append("\n".join(current_block).rstrip())

    return "\n".join(block for block in blocks if block).rstrip()


def _truncate_line_to_budget(line: str, token_budget: int) -> str:
    if token_budget <= 0:
        return ""

    pieces = TOKEN_PIECE_RE.findall(line)
    if not pieces:
        return ""

    selected: list[str] = []
    for piece in pieces:
        trial = "".join(
            selected + [piece]
        )
        if estimate_tokens(trial) > token_budget:
            break
        selected.append(piece)

    if not selected:
        return ""

    truncated = "".join(selected).strip()
    if truncated != line.strip():
        truncated = truncated.rstrip() + " ..."
    return truncated


def _anchor_terms(anchors: Sequence[ImpactAnchor]) -> set[str]:
    terms = {
        anchor.name
        for anchor in anchors
        if anchor.name and anchor.kind != "module"
    }
    terms.update(
        anchor.qualified_name
        for anchor in anchors
        if anchor.qualified_name and anchor.kind != "module"
    )
    return terms


def _budget_match_order(matches: Sequence[ContextMatch]) -> list[ContextMatch]:
    if not matches:
        return []

    grouped_matches: dict[str, list[ContextMatch]] = {}
    path_order: list[str] = []

    for match in matches:
        if match.path not in grouped_matches:
            grouped_matches[match.path] = []
            path_order.append(match.path)
        grouped_matches[match.path].append(match)

    ordered: list[ContextMatch] = []
    depth = 0
    while True:
        added_any = False
        for path in path_order:
            path_matches = grouped_matches[path]
            if depth >= len(path_matches):
                continue
            ordered.append(path_matches[depth])
            added_any = True
        if not added_any:
            break
        depth += 1

    return ordered


def _render_limited_bundle(
    anchors: Sequence[ImpactAnchor],
    matches: Sequence[LimitedContextMatch],
) -> str:
    sections: list[str] = []

    if anchors:
        anchor_lines = [
            f"- {anchor.path}:{anchor.start_line}-{anchor.end_line} [{anchor.kind}] {anchor.qualified_name}"
            for anchor in anchors
        ]
        sections.append("## Impact Anchors\n" + "\n".join(anchor_lines))

    if matches:
        blocks = [
            _render_match_block(match, match.content, truncated=match.truncated)
            for match in matches
        ]
        sections.append("\n\n".join(blocks))

    return "\n\n".join(section for section in sections if section)


def _render_match_header(match: ContextMatch | LimitedContextMatch, *, truncated: bool) -> str:
    suffix = " truncated" if truncated else ""
    return (
        f"# {match.path}:{match.start_line}-{match.end_line} "
        f"[{match.reason} score={match.score:.4f}{suffix}]"
    )


def _render_match_block(
    match: ContextMatch | LimitedContextMatch,
    content: str,
    *,
    truncated: bool,
) -> str:
    return f"{_render_match_header(match, truncated=truncated)}\n{content}".rstrip()
