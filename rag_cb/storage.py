from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(slots=True)
class FileRecord:
    path: str
    checksum: str
    modified_time: float
    size: int


@dataclass(slots=True)
class StoredChunk:
    chunk_id: int
    path: str
    start_line: int
    end_line: int
    content: str
    embedding: list[float]


@dataclass(slots=True)
class SearchHit:
    chunk_id: int
    path: str
    start_line: int
    end_line: int
    content: str
    score: float


class CodeIndex:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    modified_time REAL NOT NULL,
                    size INTEGER NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)"
            )
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chunks_path_lines
                ON chunks(path, start_line, end_line)
                """
            )

    def clear(self) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM chunks")
            self.connection.execute("DELETE FROM files")
            self.connection.execute("DELETE FROM metadata")

    def get_metadata(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def set_metadata(self, key: str, value: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO metadata(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def file_records(self) -> dict[str, FileRecord]:
        rows = self.connection.execute(
            "SELECT path, checksum, modified_time, size FROM files"
        ).fetchall()
        return {
            str(row["path"]): FileRecord(
                path=str(row["path"]),
                checksum=str(row["checksum"]),
                modified_time=float(row["modified_time"]),
                size=int(row["size"]),
            )
            for row in rows
        }

    def delete_file(self, path: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM chunks WHERE path = ?", (path,))
            self.connection.execute("DELETE FROM files WHERE path = ?", (path,))

    def replace_file_chunks(
        self,
        *,
        path: str,
        checksum: str,
        modified_time: float,
        size: int,
        chunks: Iterable[tuple[int, int, str, Sequence[float]]],
    ) -> int:
        prepared_chunks = [
            (path, start_line, end_line, content, json.dumps(list(embedding)))
            for start_line, end_line, content, embedding in chunks
        ]

        with self.connection:
            self.connection.execute("DELETE FROM chunks WHERE path = ?", (path,))
            self.connection.execute(
                """
                INSERT INTO files(path, checksum, modified_time, size)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    checksum = excluded.checksum,
                    modified_time = excluded.modified_time,
                    size = excluded.size
                """,
                (path, checksum, modified_time, size),
            )
            if prepared_chunks:
                self.connection.executemany(
                    """
                    INSERT INTO chunks(path, start_line, end_line, content, embedding)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    prepared_chunks,
                )

        return len(prepared_chunks)

    def _stored_chunk_from_row(self, row: sqlite3.Row) -> StoredChunk:
        return StoredChunk(
            chunk_id=int(row["id"]),
            path=str(row["path"]),
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            content=str(row["content"]),
            embedding=[float(value) for value in json.loads(str(row["embedding"]))],
        )

    def fetch_chunks(self, paths: Sequence[str] | None = None) -> list[StoredChunk]:
        if paths:
            placeholders = ",".join("?" for _ in paths)
            rows = self.connection.execute(
                f"""
                SELECT id, path, start_line, end_line, content, embedding
                FROM chunks
                WHERE path IN ({placeholders})
                """,
                tuple(paths),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT id, path, start_line, end_line, content, embedding FROM chunks"
            ).fetchall()

        return [self._stored_chunk_from_row(row) for row in rows]

    def fetch_chunks_for_line_window(
        self,
        path: str,
        start_line: int,
        end_line: int,
        *,
        limit: int = 3,
    ) -> list[StoredChunk]:
        rows = self.connection.execute(
            """
            SELECT id, path, start_line, end_line, content, embedding
            FROM chunks
            WHERE path = ?
              AND start_line <= ?
              AND end_line >= ?
            ORDER BY ABS(start_line - ?) + ABS(end_line - ?)
            LIMIT ?
            """,
            (path, end_line, start_line, start_line, end_line, limit),
        ).fetchall()
        return [self._stored_chunk_from_row(row) for row in rows]

    def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int = 8,
        paths: Sequence[str] | None = None,
        preferred_paths: Sequence[str] | None = None,
        preferred_path_boost: float = 0.12,
    ) -> list[SearchHit]:
        candidates = self.fetch_chunks(paths=paths)
        preferred = set(preferred_paths or [])
        scored_hits: list[SearchHit] = []

        for chunk in candidates:
            score = cosine_similarity(query_embedding, chunk.embedding)
            if chunk.path in preferred:
                score += preferred_path_boost
            scored_hits.append(
                SearchHit(
                    chunk_id=chunk.chunk_id,
                    path=chunk.path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    content=chunk.content,
                    score=score,
                )
            )

        scored_hits.sort(key=lambda hit: (-hit.score, hit.path, hit.start_line))
        return scored_hits[:top_k]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        return 0.0

    dot_product = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot_product / (left_norm * right_norm)
