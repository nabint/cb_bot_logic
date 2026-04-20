from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .storage import CodeIndex

DEFAULT_DB_DIRNAME = ".rag_cb_index"
DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

TEXT_FILE_SUFFIXES = {
    ".c",
    ".cfg",
    ".cpp",
    ".cs",
    ".gd",
    ".gdns",
    ".gdnlib",
    ".go",
    ".h",
    ".hpp",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

TEXT_FILENAMES = {"Makefile", "Dockerfile"}
IGNORED_DIRECTORIES = {".git", "__pycache__", DEFAULT_DB_DIRNAME, "rag_cb"}
IGNORED_SUFFIXES = {
    ".a",
    ".class",
    ".dylib",
    ".exe",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".o",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".svg",
    ".ttf",
    ".wav",
    ".zip",
}


@dataclass(slots=True)
class CodeChunk:
    path: str
    start_line: int
    end_line: int
    content: str
    embedding_text: str


@dataclass(slots=True)
class IndexStats:
    db_path: str
    files_seen: int
    files_indexed: int
    files_skipped: int
    files_removed: int
    chunks_indexed: int
    model_name: str


class SentenceTransformerEncoder:
    def __init__(self, model_name: str = DEFAULT_MODEL_NAME):
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ImportError(
                    "sentence-transformers is required to build or query the rag_cb index. "
                    "Install it with `pip install sentence-transformers`."
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        model = self._load_model()
        embeddings = model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        encoded: list[list[float]] = []
        for embedding in embeddings:
            if hasattr(embedding, "tolist"):
                encoded.append([float(value) for value in embedding.tolist()])
            else:
                encoded.append([float(value) for value in embedding])
        return encoded


def default_db_path(repo_path: str | Path) -> Path:
    repo_root = Path(repo_path).resolve()
    return repo_root / DEFAULT_DB_DIRNAME / "code_index.sqlite3"


def index_repository(
    repo_path: str | Path = ".",
    *,
    db_path: str | Path | None = None,
    model_name: str = DEFAULT_MODEL_NAME,
    chunk_size_lines: int = 80,
    chunk_overlap_lines: int = 20,
    include_patterns: Sequence[str] | None = None,
    exclude_patterns: Sequence[str] | None = None,
    max_file_bytes: int = 1_000_000,
) -> IndexStats:
    repo_root = Path(repo_path).resolve()
    resolved_db_path = Path(db_path).resolve() if db_path else default_db_path(repo_root)

    encoder = SentenceTransformerEncoder(model_name=model_name)
    index = CodeIndex(resolved_db_path)

    try:
        previous_model_name = index.get_metadata("model_name")
        if previous_model_name and previous_model_name != model_name:
            index.clear()

        existing_records = index.file_records()
        seen_paths: set[str] = set()
        files_seen = 0
        files_indexed = 0
        files_skipped = 0
        chunks_indexed = 0

        for file_path in iter_repository_files(
            repo_root,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            max_file_bytes=max_file_bytes,
        ):
            files_seen += 1
            relative_path = file_path.relative_to(repo_root).as_posix()
            seen_paths.add(relative_path)

            text = file_path.read_text(encoding="utf-8", errors="ignore")
            checksum = hashlib.sha1(text.encode("utf-8")).hexdigest()
            stat = file_path.stat()
            existing = existing_records.get(relative_path)
            if existing and existing.checksum == checksum and previous_model_name == model_name:
                files_skipped += 1
                continue

            code_chunks = chunk_source(
                relative_path,
                text,
                chunk_size_lines=chunk_size_lines,
                chunk_overlap_lines=chunk_overlap_lines,
            )
            embeddings = encoder.encode([chunk.embedding_text for chunk in code_chunks])
            chunk_rows = [
                (chunk.start_line, chunk.end_line, chunk.content, embedding)
                for chunk, embedding in zip(code_chunks, embeddings)
            ]
            inserted_chunks = index.replace_file_chunks(
                path=relative_path,
                checksum=checksum,
                modified_time=stat.st_mtime,
                size=stat.st_size,
                chunks=chunk_rows,
            )
            files_indexed += 1
            chunks_indexed += inserted_chunks

        files_removed = 0
        for stored_path in existing_records:
            if stored_path not in seen_paths:
                index.delete_file(stored_path)
                files_removed += 1

        index.set_metadata("model_name", model_name)
        index.set_metadata("repo_root", str(repo_root))
        index.set_metadata("indexed_at", datetime.now(timezone.utc).isoformat())

        return IndexStats(
            db_path=str(resolved_db_path),
            files_seen=files_seen,
            files_indexed=files_indexed,
            files_skipped=files_skipped,
            files_removed=files_removed,
            chunks_indexed=chunks_indexed,
            model_name=model_name,
        )
    finally:
        index.close()


def iter_repository_files(
    repo_root: Path,
    *,
    include_patterns: Sequence[str] | None = None,
    exclude_patterns: Sequence[str] | None = None,
    max_file_bytes: int = 1_000_000,
) -> Iterable[Path]:
    for current_root, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            directory
            for directory in dirnames
            if directory not in IGNORED_DIRECTORIES
        ]

        for filename in filenames:
            candidate = Path(current_root) / filename
            relative_path = candidate.relative_to(repo_root).as_posix()

            if not should_index_file(
                candidate,
                relative_path,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                max_file_bytes=max_file_bytes,
            ):
                continue

            yield candidate


def should_index_file(
    path: Path,
    relative_path: str,
    *,
    include_patterns: Sequence[str] | None = None,
    exclude_patterns: Sequence[str] | None = None,
    max_file_bytes: int = 1_000_000,
) -> bool:
    if include_patterns and not matches_any(relative_path, include_patterns):
        return False

    effective_excludes = list(exclude_patterns or [])
    if matches_any(relative_path, effective_excludes):
        return False

    if path.suffix.lower() in IGNORED_SUFFIXES:
        return False

    if path.name not in TEXT_FILENAMES and path.suffix.lower() not in TEXT_FILE_SUFFIXES:
        return False

    stat = path.stat()
    if stat.st_size > max_file_bytes:
        return False

    sample = path.read_bytes()[:2048]
    if b"\x00" in sample:
        return False

    return True


def matches_any(relative_path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(relative_path, pattern) for pattern in patterns)


def chunk_source(
    relative_path: str,
    text: str,
    *,
    chunk_size_lines: int = 80,
    chunk_overlap_lines: int = 20,
) -> list[CodeChunk]:
    if chunk_size_lines <= 0:
        raise ValueError("chunk_size_lines must be greater than zero")
    if chunk_overlap_lines < 0:
        raise ValueError("chunk_overlap_lines cannot be negative")
    if chunk_overlap_lines >= chunk_size_lines:
        raise ValueError("chunk_overlap_lines must be smaller than chunk_size_lines")

    lines = text.splitlines()
    if not lines:
        return []

    step = max(1, chunk_size_lines - chunk_overlap_lines)
    chunks: list[CodeChunk] = []

    for start_index in range(0, len(lines), step):
        end_index = min(len(lines), start_index + chunk_size_lines)
        chunk_lines = lines[start_index:end_index]
        content = "\n".join(chunk_lines).rstrip()
        if not content.strip():
            if end_index == len(lines):
                break
            continue

        start_line = start_index + 1
        end_line = end_index
        embedding_text = (
            f"File: {relative_path}\n"
            f"Lines: {start_line}-{end_line}\n"
            f"{content}"
        )
        chunks.append(
            CodeChunk(
                path=relative_path,
                start_line=start_line,
                end_line=end_line,
                content=content,
                embedding_text=embedding_text,
            )
        )

        if end_index == len(lines):
            break

    return chunks
