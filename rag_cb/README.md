# rag_cb

`rag_cb` adds a lightweight retrieval layer for this repository:

- `index_repository(...)` walks the repo, chunks code files, embeds them with a SentenceTransformer, and stores the result in a local SQLite database.
- `get_context(...)` accepts a `git diff` patch, finds the changed files and hunk line ranges, then returns the most relevant code chunks from the index.

The default database path is `.rag_cb_index/code_index.sqlite3` in the repo root.
By default, indexing walks the whole current repo except ignored directories such as `.git`, `__pycache__`, `.rag_cb_index`, and `rag_cb`.

## Install

`rag_cb` imports `sentence-transformers` lazily, so regular imports are safe even before the dependency is installed. To actually build or query the index:

```bash
pip install sentence-transformers
```

## Python Usage

```python
from rag_cb import get_context, index_repository

index_repository(".")

with open("/tmp/my.diff", "r", encoding="utf-8") as handle:
    diff_text = handle.read()

context = get_context(diff_text, ".")
print(context.render())
```

## CLI Usage

```bash
python -m rag_cb index --repo .
git diff | python -m rag_cb context --repo . --auto-build
```
