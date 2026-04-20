from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .indexer import DEFAULT_MODEL_NAME, SentenceTransformerEncoder, default_db_path, index_repository
from .snippets import extract_focus_snippets
from .storage import CodeIndex, SearchHit, StoredChunk
from .symbols import SymbolSpan, extract_symbol_spans, find_innermost_symbol, find_overlapping_symbols

HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")

REASON_PRIORITY = {
    "changed_region": 0,
    "enclosing_symbol": 1,
    "symbol_definition": 2,
    "symbol_callsite": 3,
    "module_import": 4,
    "symbol_reference": 5,
    "same_file_context": 6,
    "changed_file_semantic": 7,
    "semantic_match": 8,
}


@dataclass(slots=True)
class ImpactAnchor:
    path: str
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    query_text: str


@dataclass(slots=True)
class ContextMatch:
    path: str
    start_line: int
    end_line: int
    score: float
    reason: str
    content: str
    chunk_id: int | None = None


@dataclass(slots=True)
class ContextBundle:
    changed_files: list[str]
    anchors: list[ImpactAnchor] = field(default_factory=list)
    matches: list[ContextMatch] = field(default_factory=list)

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
            match_blocks: list[str] = []
            for match in self.matches:
                header = (
                    f"# {match.path}:{match.start_line}-{match.end_line} "
                    f"[{match.reason} score={match.score:.4f}]"
                )
                match_blocks.append(f"{header}\n{match.content}")
            sections.append("\n\n".join(match_blocks))

        rendered = "\n\n".join(section for section in sections if section)
        if max_chars is not None and len(rendered) > max_chars:
            return rendered[:max_chars].rstrip() + "\n..."
        return rendered

    def to_dict(self) -> dict[str, object]:
        return {
            "changed_files": list(self.changed_files),
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
                    "content": match.content,
                }
                for match in self.matches
            ],
            "context_text": self.context_text,
        }


@dataclass(slots=True)
class ParsedDiff:
    changed_files: list[str]
    file_queries: dict[str, str]
    changed_lines: dict[str, list[str]]
    line_windows: dict[str, list[tuple[int, int]]]
    global_query: str


def get_context(
    git_diff: str,
    repo_path: str | Path = ".",
    *,
    db_path: str | Path | None = None,
    model_name: str = DEFAULT_MODEL_NAME,
    top_k: int = 8,
    per_file_top_k: int = 2,
    auto_build: bool = False,
) -> ContextBundle:
    repo_root = Path(repo_path).resolve()
    resolved_db_path = Path(db_path).resolve() if db_path else default_db_path(repo_root)
    parsed_diff = parse_git_diff(git_diff)
    anchors = _build_impact_anchors(parsed_diff, repo_root)

    if auto_build and not resolved_db_path.exists():
        index_repository(repo_root, db_path=resolved_db_path, model_name=model_name)

    index = CodeIndex(resolved_db_path)
    try:
        indexed_model_name = index.get_metadata("model_name")
        if indexed_model_name is None:
            if auto_build:
                index.close()
                index_repository(repo_root, db_path=resolved_db_path, model_name=model_name)
                index = CodeIndex(resolved_db_path)
            else:
                raise FileNotFoundError(
                    f"No rag_cb index found at {resolved_db_path}. "
                    "Run index_repository(...) first or call get_context(..., auto_build=True)."
                )
        elif indexed_model_name != model_name and auto_build:
            index.close()
            index_repository(repo_root, db_path=resolved_db_path, model_name=model_name)
            index = CodeIndex(resolved_db_path)
        elif indexed_model_name != model_name:
            raise ValueError(
                f"Index at {resolved_db_path} was built with {indexed_model_name}, "
                f"but get_context is using {model_name}."
            )

        collected_matches: dict[str, ContextMatch] = {}
        supported_anchors: list[ImpactAnchor] = []

        for anchor in anchors:
            exact_matches = _find_exact_impact_matches(
                index,
                anchor,
                repo_root=repo_root,
                top_k=max(8, per_file_top_k * 6),
                per_path_limit=max(1, per_file_top_k),
            )
            exact_matches = _filter_exact_matches(anchor, exact_matches)
            if not _has_strong_external_support(anchor, exact_matches):
                continue

            supported_anchors.append(anchor)
            _merge_matches(
                collected_matches,
                [_build_anchor_scope_match(anchor, repo_root, reason="changed_region", score=1.0)],
            )
            _merge_matches(
                collected_matches,
                _find_anchor_local_context(index, anchor, repo_root=repo_root),
            )
            _merge_matches(collected_matches, exact_matches)

        if not supported_anchors:
            return ContextBundle(
                changed_files=parsed_diff.changed_files,
                anchors=[],
                matches=[],
            )

        semantic_queries = [anchor.query_text for anchor in supported_anchors if anchor.query_text.strip()]
        if not semantic_queries and parsed_diff.global_query.strip():
            semantic_queries = [parsed_diff.global_query]

        if semantic_queries:
            try:
                encoder = SentenceTransformerEncoder(model_name=model_name)
                embeddings = encoder.encode(semantic_queries)
            except ImportError:
                embeddings = []

            if embeddings:
                if supported_anchors:
                    semantic_limit = max(2, top_k // max(1, len(supported_anchors)))
                    for anchor, embedding in zip(supported_anchors, embeddings):
                        hits = index.search(
                            embedding,
                            top_k=semantic_limit,
                            preferred_paths=[anchor.path],
                            preferred_path_boost=0.18,
                        )
                        _merge_matches(
                            collected_matches,
                            [
                                _from_search_hit(
                                    hit,
                                    reason=(
                                        "changed_file_semantic"
                                        if hit.path == anchor.path
                                        else "semantic_match"
                                    ),
                                )
                                for hit in hits
                            ],
                        )
                else:
                    hits = index.search(
                        embeddings[0],
                        top_k=top_k,
                        preferred_paths=parsed_diff.changed_files,
                    )
                    _merge_matches(
                        collected_matches,
                        [
                            _from_search_hit(
                                hit,
                                reason=(
                                    "changed_file_semantic"
                                    if hit.path in parsed_diff.changed_files
                                    else "semantic_match"
                                ),
                            )
                            for hit in hits
                        ],
                    )

        final_matches = sorted(
            collected_matches.values(),
            key=lambda match: (
                REASON_PRIORITY.get(match.reason, 99),
                -match.score,
                match.path,
                match.start_line,
            ),
        )
        return ContextBundle(
            changed_files=parsed_diff.changed_files,
            anchors=supported_anchors,
            matches=final_matches,
        )
    finally:
        index.close()


def parse_git_diff(git_diff: str) -> ParsedDiff:
    changed_files: list[str] = []
    changed_lines: dict[str, list[str]] = defaultdict(list)
    line_windows: dict[str, list[tuple[int, int]]] = defaultdict(list)
    current_path: str | None = None
    current_new_line: int | None = None

    for raw_line in git_diff.splitlines():
        line = raw_line.rstrip("\n")
        header_match = DIFF_HEADER_RE.match(line)
        if header_match:
            old_path = header_match.group(1)
            new_path = header_match.group(2)
            current_path = new_path if new_path != "/dev/null" else old_path
            if current_path and current_path not in changed_files:
                changed_files.append(current_path)
            continue

        if line.startswith("+++ "):
            if line.startswith("+++ b/"):
                current_path = line[6:]
            if current_path and current_path not in changed_files:
                changed_files.append(current_path)
            continue

        if current_path is None:
            continue

        if line.startswith("@@"):
            changed_lines[current_path].append(line)
            hunk_match = HUNK_HEADER_RE.match(line)
            if hunk_match:
                current_new_line = max(1, int(hunk_match.group(1)))
            continue

        if line.startswith(("diff --git", "index ", "--- ")):
            continue

        if line[:1] in {"+", "-", " "}:
            content = line[1:].rstrip()
            if content.strip():
                changed_lines[current_path].append(content)

            if current_new_line is None:
                continue

            if line.startswith("+"):
                line_windows[current_path].append((current_new_line, current_new_line))
                current_new_line += 1
            elif line.startswith("-"):
                affected_line = max(1, current_new_line)
                line_windows[current_path].append((affected_line, affected_line))
            else:
                current_new_line += 1

    if not changed_files and git_diff.strip():
        global_query = git_diff.strip()
    else:
        global_query = "\n\n".join(
            f"File: {path}\n" + "\n".join(changed_lines.get(path, [])[:80])
            for path in changed_files
            if changed_lines.get(path)
        )

    file_queries = {
        path: f"File: {path}\n" + "\n".join(lines[:80])
        for path, lines in changed_lines.items()
    }

    return ParsedDiff(
        changed_files=changed_files,
        file_queries=file_queries,
        changed_lines={path: lines[:] for path, lines in changed_lines.items()},
        line_windows={path: windows[:] for path, windows in line_windows.items()},
        global_query=global_query,
    )


def _build_impact_anchors(parsed_diff: ParsedDiff, repo_root: Path) -> list[ImpactAnchor]:
    anchors_by_key: dict[tuple[str, str, str, int, int], ImpactAnchor] = {}

    for path in parsed_diff.changed_files:
        file_path = repo_root / path
        file_text = _safe_read_text(file_path)
        symbols = extract_symbol_spans(path, file_text) if file_text else []
        windows = parsed_diff.line_windows.get(path) or [(1, 1)]

        for start_line, end_line in windows:
            anchor_symbols = _select_anchor_symbols(symbols, start_line, end_line)
            if not anchor_symbols:
                anchor_symbols = [
                    SymbolSpan(
                        name=_module_display_name(path),
                        qualified_name=_module_display_name(path),
                        kind="module",
                        start_line=start_line,
                        end_line=end_line,
                        is_top_level=True,
                    )
                ]

            for symbol in anchor_symbols:
                anchor = ImpactAnchor(
                    path=path,
                    name=symbol.name,
                    qualified_name=symbol.qualified_name,
                    kind=symbol.kind,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    query_text=_build_anchor_query(symbol, path, parsed_diff.file_queries.get(path, "")),
                )
                anchors_by_key[(anchor.path, anchor.kind, anchor.qualified_name, anchor.start_line, anchor.end_line)] = anchor

    anchors = list(anchors_by_key.values())
    anchors.sort(key=lambda anchor: (_anchor_priority(anchor.kind), anchor.path, anchor.start_line, anchor.qualified_name))
    return anchors


def _select_anchor_symbols(symbols: list[SymbolSpan], start_line: int, end_line: int) -> list[SymbolSpan]:
    enclosing_symbol = find_innermost_symbol(
        symbols,
        start_line,
        end_line,
        allowed_kinds={"function", "method", "class"},
    )
    if enclosing_symbol is not None:
        return [enclosing_symbol]

    global_symbols = find_overlapping_symbols(
        symbols,
        start_line,
        end_line,
        allowed_kinds={"global"},
    )
    if global_symbols:
        return global_symbols

    return []


def _build_anchor_query(symbol: SymbolSpan, path: str, diff_query: str) -> str:
    parts = [
        f"File: {path}",
        f"Anchor Kind: {symbol.kind}",
        f"Anchor: {symbol.qualified_name}",
    ]
    module_name = _module_import_name(path)
    if module_name:
        parts.append(f"Module: {module_name}")
    if diff_query.strip():
        parts.append(diff_query.strip())
    return "\n".join(parts)


def _find_anchor_local_context(
    index: CodeIndex,
    anchor: ImpactAnchor,
    *,
    repo_root: Path,
) -> list[ContextMatch]:
    if anchor.kind != "module":
        return [_build_anchor_scope_match(anchor, repo_root, reason="enclosing_symbol", score=0.995)]

    estimated_chunks = max(2, (max(1, anchor.end_line - anchor.start_line + 1) // 50) + 2)
    reason = "enclosing_symbol" if anchor.kind != "module" else "same_file_context"
    score = 0.995 if anchor.kind != "module" else 0.97
    return [
        _from_stored_chunk(chunk, score=score, reason=reason)
        for chunk in index.fetch_chunks_for_line_window(
            anchor.path,
            anchor.start_line,
            anchor.end_line,
            limit=estimated_chunks,
        )
    ]


def _find_exact_impact_matches(
    index: CodeIndex,
    anchor: ImpactAnchor,
    *,
    repo_root: Path,
    top_k: int,
    per_path_limit: int,
) -> list[ContextMatch]:
    symbol_pattern = (
        re.compile(rf"\b{re.escape(anchor.name)}\b")
        if anchor.kind != "module"
        else None
    )
    call_pattern = (
        re.compile(rf"\b{re.escape(anchor.name)}\s*\(")
        if anchor.kind in {"function", "method"}
        else None
    )
    definition_patterns = _definition_patterns(anchor)
    import_patterns = _import_patterns(anchor)
    file_cache: dict[str, tuple[str, list[SymbolSpan]]] = {}
    import_support_cache: dict[str, bool] = {}

    candidates: list[ContextMatch] = []
    for chunk in index.fetch_chunks():
        if chunk.path == anchor.path and _chunk_overlaps_anchor(chunk, anchor):
            continue

        symbol_hits = len(symbol_pattern.findall(chunk.content)) if symbol_pattern else 0
        import_hit = any(pattern.search(chunk.content) for pattern in import_patterns)
        if symbol_hits == 0 and not import_hit:
            continue

        file_text, _ = _load_file_symbols(chunk.path, repo_root, file_cache)
        if _requires_import_evidence(anchor, chunk.path) and not _file_has_anchor_import(
            anchor=anchor,
            relative_path=chunk.path,
            file_text=file_text,
            import_patterns=import_patterns,
            cache=import_support_cache,
        ):
            continue

        callsite_lines = (
            _match_line_numbers(
                chunk,
                call_pattern,
                exclude_patterns=definition_patterns,
            )
            if call_pattern
            else []
        )
        if callsite_lines:
            scoped_matches = _build_callsite_scope_matches(
                anchor=anchor,
                chunk=chunk,
                repo_root=repo_root,
                file_cache=file_cache,
                matched_lines=callsite_lines,
            )
            if scoped_matches:
                candidates.extend(scoped_matches)
                continue

        if any(pattern.search(chunk.content) for pattern in definition_patterns):
            reason = "symbol_definition"
            score = 0.98
        elif import_hit:
            reason = "module_import"
            score = 0.95
        elif chunk.path == anchor.path:
            reason = "same_file_context"
            score = 0.93
        else:
            reason = "symbol_reference"
            score = 0.91

        score += min(0.03, 0.01 * max(0, symbol_hits - 1))
        candidates.append(
            ContextMatch(
                chunk_id=chunk.chunk_id,
                path=chunk.path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                score=score,
                reason=reason,
                content=chunk.content,
            )
        )

    candidates.sort(
        key=lambda match: (
            REASON_PRIORITY.get(match.reason, 99),
            -match.score,
            match.path,
            match.start_line,
        )
    )
    return _rank_exact_matches(
        candidates,
        top_k=top_k,
        per_path_limit=per_path_limit,
    )


def _match_line_numbers(
    chunk: StoredChunk,
    pattern: re.Pattern[str] | None,
    *,
    exclude_patterns: list[re.Pattern[str]] | None = None,
) -> list[int]:
    if pattern is None:
        return []

    matched_lines: list[int] = []
    for offset, line in enumerate(chunk.content.splitlines()):
        if exclude_patterns and any(exclude.search(line) for exclude in exclude_patterns):
            continue
        if pattern.search(line):
            matched_lines.append(chunk.start_line + offset)
    return matched_lines


def _build_callsite_scope_matches(
    *,
    anchor: ImpactAnchor,
    chunk: StoredChunk,
    repo_root: Path,
    file_cache: dict[str, tuple[str, list[SymbolSpan]]],
    matched_lines: list[int],
) -> list[ContextMatch]:
    file_text, _ = _load_file_symbols(chunk.path, repo_root, file_cache)
    if not file_text:
        return []

    snippets = extract_focus_snippets(
        chunk.path,
        file_text,
        matched_lines,
        max_lines=16,
    )
    matches: list[ContextMatch] = []
    seen_spans: set[tuple[int, int]] = set()

    for snippet in snippets:
        if (
            chunk.path == anchor.path
            and snippet.start_line <= anchor.end_line
            and snippet.end_line >= anchor.start_line
        ):
            continue

        span_key = (snippet.start_line, snippet.end_line)
        if span_key in seen_spans:
            continue
        seen_spans.add(span_key)
        matches.append(
            ContextMatch(
                chunk_id=None,
                path=chunk.path,
                start_line=snippet.start_line,
                end_line=snippet.end_line,
                score=0.97,
                reason="symbol_callsite",
                content=snippet.content,
            )
        )

    return matches


def _build_anchor_scope_match(
    anchor: ImpactAnchor,
    repo_root: Path,
    *,
    reason: str,
    score: float,
) -> ContextMatch:
    file_text = _safe_read_text(repo_root / anchor.path)
    snippet = _slice_file_text(
        file_text,
        start_line=anchor.start_line,
        end_line=anchor.end_line,
    )
    return ContextMatch(
        chunk_id=None,
        path=anchor.path,
        start_line=anchor.start_line,
        end_line=anchor.end_line,
        score=score,
        reason=reason,
        content=snippet,
    )


def _has_strong_external_support(
    anchor: ImpactAnchor,
    matches: list[ContextMatch],
) -> bool:
    strong_reasons = {
        "function": {"symbol_callsite", "symbol_reference", "symbol_definition"},
        "method": {"symbol_callsite", "symbol_reference", "symbol_definition"},
        "class": {"symbol_reference", "symbol_definition"},
        "global": {"symbol_reference", "symbol_definition", "module_import"},
        "module": {"symbol_reference", "symbol_definition", "module_import"},
    }.get(anchor.kind, {"symbol_reference", "symbol_definition"})

    for match in matches:
        if match.path == anchor.path:
            continue
        if match.reason in strong_reasons:
            return True
    return False


def _filter_exact_matches(
    anchor: ImpactAnchor,
    matches: list[ContextMatch],
) -> list[ContextMatch]:
    if anchor.kind not in {"function", "method", "class"}:
        return matches

    return [
        match
        for match in matches
        if match.reason != "module_import"
    ]


def _definition_patterns(anchor: ImpactAnchor) -> list[re.Pattern[str]]:
    escaped_name = re.escape(anchor.name)
    patterns = [
        re.compile(rf"^\s*(?:async\s+def|def|func)\s+{escaped_name}\b", re.MULTILINE),
        re.compile(rf"^\s*class\s+{escaped_name}\b", re.MULTILINE),
        re.compile(rf"^\s*class_name\s+{escaped_name}\b", re.MULTILINE),
        re.compile(rf"^\s*{escaped_name}\s*=", re.MULTILINE),
        re.compile(
            rf"^\s*(?:static\s+)?(?:const\s+)?(?:[A-Za-z_][\w\s\*\[\]]+\s+)+{escaped_name}\s*(?:=|;|\()",
            re.MULTILINE,
        ),
    ]
    return patterns


def _import_patterns(anchor: ImpactAnchor) -> list[re.Pattern[str]]:
    module_candidates = _module_import_candidates(anchor.path)
    patterns: list[re.Pattern[str]] = []

    for module_name in module_candidates:
        escaped_module = re.escape(module_name)
        patterns.append(re.compile(rf"\bimport\s+{escaped_module}\b"))
        patterns.append(re.compile(rf"\bfrom\s+{escaped_module}\s+import\b"))
        if "." in module_name:
            parent_module, leaf_module = module_name.rsplit(".", 1)
            patterns.append(
                re.compile(
                    rf"\bfrom\s+{re.escape(parent_module)}\s+import\s+[^\n#]*\b{re.escape(leaf_module)}\b"
                )
            )
        if anchor.kind != "module":
            escaped_name = re.escape(anchor.name)
            patterns.append(
                re.compile(rf"\bfrom\s+{escaped_module}\s+import\s+[^\n#]*\b{escaped_name}\b")
            )

    return patterns


def _module_import_candidates(path: str) -> list[str]:
    file_path = Path(path)
    candidates: list[str] = []

    if file_path.suffix == ".py":
        parts = list(file_path.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            candidates.append(".".join(parts))

    stem = file_path.stem
    if stem and stem not in candidates:
        candidates.append(stem)

    return candidates


def _module_import_name(path: str) -> str | None:
    candidates = _module_import_candidates(path)
    if not candidates:
        return None
    return candidates[0]


def _module_display_name(path: str) -> str:
    return _module_import_name(path) or Path(path).stem or path


def _chunk_overlaps_anchor(chunk: StoredChunk, anchor: ImpactAnchor) -> bool:
    if chunk.path != anchor.path:
        return False
    return chunk.start_line <= anchor.end_line and chunk.end_line >= anchor.start_line


def _anchor_priority(kind: str) -> int:
    priorities = {
        "function": 0,
        "method": 0,
        "class": 1,
        "global": 2,
        "module": 3,
    }
    return priorities.get(kind, 9)


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _load_file_symbols(
    relative_path: str,
    repo_root: Path,
    cache: dict[str, tuple[str, list[SymbolSpan]]],
) -> tuple[str, list[SymbolSpan]]:
    cached = cache.get(relative_path)
    if cached is not None:
        return cached

    file_path = repo_root / relative_path
    file_text = _safe_read_text(file_path)
    symbols = extract_symbol_spans(relative_path, file_text) if file_text else []
    cache[relative_path] = (file_text, symbols)
    return cache[relative_path]


def _slice_file_text(file_text: str, *, start_line: int, end_line: int) -> str:
    lines = file_text.splitlines()
    if not lines:
        return ""
    start_index = max(0, start_line - 1)
    end_index = min(len(lines), end_line)
    return "\n".join(lines[start_index:end_index]).rstrip()


def _from_search_hit(hit: SearchHit, *, reason: str) -> ContextMatch:
    return ContextMatch(
        chunk_id=hit.chunk_id,
        path=hit.path,
        start_line=hit.start_line,
        end_line=hit.end_line,
        score=hit.score,
        reason=reason,
        content=hit.content,
    )


def _from_stored_chunk(chunk: StoredChunk, *, score: float, reason: str) -> ContextMatch:
    return ContextMatch(
        chunk_id=chunk.chunk_id,
        path=chunk.path,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        score=score,
        reason=reason,
        content=chunk.content,
    )


def _merge_matches(
    collected_matches: dict[str, ContextMatch],
    candidates: Iterable[ContextMatch],
) -> None:
    for candidate in candidates:
        key = _match_key(candidate)
        existing = collected_matches.get(key)
        if existing is None:
            collected_matches[key] = candidate
            continue

        existing_priority = REASON_PRIORITY.get(existing.reason, 99)
        candidate_priority = REASON_PRIORITY.get(candidate.reason, 99)
        if candidate_priority < existing_priority:
            collected_matches[key] = candidate
            continue

        if candidate_priority == existing_priority and candidate.score > existing.score:
            collected_matches[key] = candidate


def _match_key(match: ContextMatch) -> str:
    if match.chunk_id is not None:
        return f"chunk:{match.chunk_id}"
    return f"span:{match.path}:{match.start_line}:{match.end_line}"


def _requires_import_evidence(anchor: ImpactAnchor, candidate_path: str) -> bool:
    if candidate_path == anchor.path:
        return False
    if Path(anchor.path).suffix.lower() != ".py":
        return False
    return anchor.kind in {"function", "class", "global"}


def _file_has_anchor_import(
    *,
    anchor: ImpactAnchor,
    relative_path: str,
    file_text: str,
    import_patterns: list[re.Pattern[str]],
    cache: dict[str, bool],
) -> bool:
    cached = cache.get(relative_path)
    if cached is not None:
        return cached

    has_import = any(pattern.search(file_text) for pattern in import_patterns)
    cache[relative_path] = has_import
    return has_import


def _rank_exact_matches(
    matches: list[ContextMatch],
    *,
    top_k: int,
    per_path_limit: int,
) -> list[ContextMatch]:
    if not matches:
        return []

    sorted_matches = sorted(
        matches,
        key=lambda match: (
            REASON_PRIORITY.get(match.reason, 99),
            -match.score,
            match.path,
            match.start_line,
        ),
    )

    grouped_matches: dict[str, list[ContextMatch]] = {}
    path_order: list[str] = []
    capped_limit = max(1, per_path_limit)

    for match in sorted_matches:
        if match.path not in grouped_matches:
            grouped_matches[match.path] = []
            path_order.append(match.path)
        if len(grouped_matches[match.path]) >= capped_limit:
            continue
        grouped_matches[match.path].append(match)

    ranked: list[ContextMatch] = []
    depth = 0
    while len(ranked) < top_k:
        added_any = False
        for path in path_order:
            path_matches = grouped_matches[path]
            if depth >= len(path_matches):
                continue
            ranked.append(path_matches[depth])
            added_any = True
            if len(ranked) >= top_k:
                break
        if not added_any:
            break
        depth += 1

    return ranked
