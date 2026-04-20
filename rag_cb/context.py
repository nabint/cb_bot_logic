from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .indexer import DEFAULT_MODEL_NAME, SentenceTransformerEncoder, default_db_path, index_repository
from .storage import CodeIndex, SearchHit, StoredChunk

HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")

REASON_PRIORITY = {
    "diff_hunk_overlap": 0,
    "changed_file_semantic": 1,
    "semantic_match": 2,
}


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
    matches: list[ContextMatch] = field(default_factory=list)

    @property
    def context_text(self) -> str:
        return self.render()

    def render(self, *, max_chars: int | None = None) -> str:
        blocks: list[str] = []
        for match in self.matches:
            header = (
                f"# {match.path}:{match.start_line}-{match.end_line} "
                f"[{match.reason} score={match.score:.4f}]"
            )
            blocks.append(f"{header}\n{match.content}")

        rendered = "\n\n".join(blocks)
        if max_chars is not None and len(rendered) > max_chars:
            return rendered[:max_chars].rstrip() + "\n..."
        return rendered

    def to_dict(self) -> dict[str, object]:
        return {
            "changed_files": list(self.changed_files),
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
                indexed_model_name = index.get_metadata("model_name")
            else:
                raise FileNotFoundError(
                    f"No rag_cb index found at {resolved_db_path}. "
                    "Run index_repository(...) first or call get_context(..., auto_build=True)."
                )

        if indexed_model_name and indexed_model_name != model_name:
            if auto_build:
                index.close()
                index_repository(repo_root, db_path=resolved_db_path, model_name=model_name)
                index = CodeIndex(resolved_db_path)
            else:
                raise ValueError(
                    f"Index at {resolved_db_path} was built with {indexed_model_name}, "
                    f"but get_context is using {model_name}."
                )

        encoder = SentenceTransformerEncoder(model_name=model_name)
        collected_matches: dict[int, ContextMatch] = {}

        for path, line_windows in parsed_diff.line_windows.items():
            for start_line, end_line in line_windows:
                overlapping_chunks = index.fetch_chunks_for_line_window(
                    path,
                    start_line,
                    end_line,
                    limit=2,
                )
                _merge_matches(
                    collected_matches,
                    [
                        _from_stored_chunk(
                            chunk,
                            score=1.0,
                            reason="diff_hunk_overlap",
                        )
                        for chunk in overlapping_chunks
                    ],
                )

        file_level_queries = [
            (path, query_text)
            for path, query_text in parsed_diff.file_queries.items()
            if query_text.strip()
        ]
        if file_level_queries:
            embeddings = encoder.encode([query_text for _, query_text in file_level_queries])
            for (path, _), embedding in zip(file_level_queries, embeddings):
                hits = index.search(
                    embedding,
                    top_k=per_file_top_k,
                    paths=[path],
                    preferred_paths=[path],
                    preferred_path_boost=0.18,
                )
                _merge_matches(
                    collected_matches,
                    [
                        _from_search_hit(hit, reason="changed_file_semantic")
                        for hit in hits
                    ],
                )

        if parsed_diff.global_query.strip():
            query_embedding = encoder.encode([parsed_diff.global_query])[0]
            semantic_hits = index.search(
                query_embedding,
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
                    for hit in semantic_hits
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
        return ContextBundle(changed_files=parsed_diff.changed_files, matches=final_matches)
    finally:
        index.close()


def parse_git_diff(git_diff: str) -> ParsedDiff:
    changed_files: list[str] = []
    file_queries: dict[str, list[str]] = defaultdict(list)
    line_windows: dict[str, list[tuple[int, int]]] = defaultdict(list)
    current_path: str | None = None

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
            file_queries[current_path].append(line)
            hunk_match = HUNK_HEADER_RE.match(line)
            if hunk_match:
                start_line = int(hunk_match.group(1))
                span = int(hunk_match.group(2) or "1")
                if span == 0:
                    start_line = max(1, start_line)
                    end_line = start_line
                else:
                    end_line = start_line + span - 1
                line_windows[current_path].append((start_line, end_line))
            continue

        if line.startswith(("diff --git", "index ", "--- ")):
            continue

        if line[:1] in {"+", "-", " "}:
            content = line[1:].strip()
            if content:
                file_queries[current_path].append(content)

    if not changed_files and git_diff.strip():
        global_query = git_diff.strip()
    else:
        global_query_parts: list[str] = []
        for path in changed_files:
            query_bits = "\n".join(file_queries.get(path, [])[:80])
            global_query_parts.append(f"File: {path}\n{query_bits}".strip())
        global_query = "\n\n".join(part for part in global_query_parts if part.strip())

    collapsed_queries = {
        path: f"File: {path}\n" + "\n".join(lines[:80])
        for path, lines in file_queries.items()
    }

    return ParsedDiff(
        changed_files=changed_files,
        file_queries=collapsed_queries,
        line_windows={path: windows[:] for path, windows in line_windows.items()},
        global_query=global_query,
    )


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
    collected_matches: dict[int, ContextMatch],
    candidates: Iterable[ContextMatch],
) -> None:
    for candidate in candidates:
        if candidate.chunk_id is None:
            continue

        existing = collected_matches.get(candidate.chunk_id)
        if existing is None:
            collected_matches[candidate.chunk_id] = candidate
            continue

        existing_priority = REASON_PRIORITY.get(existing.reason, 99)
        candidate_priority = REASON_PRIORITY.get(candidate.reason, 99)
        if candidate_priority < existing_priority:
            collected_matches[candidate.chunk_id] = candidate
            continue

        if candidate_priority == existing_priority and candidate.score > existing.score:
            collected_matches[candidate.chunk_id] = candidate
