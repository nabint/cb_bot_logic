from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

CONTROL_FLOW_TYPES = tuple(
    node_type
    for node_type in (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        getattr(ast, "Match", None),
    )
    if node_type is not None
)
SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(slots=True)
class FocusedSnippet:
    start_line: int
    end_line: int
    content: str


def extract_focus_snippets(
    path: str,
    file_text: str,
    focus_lines: list[int],
    *,
    max_lines: int = 16,
) -> list[FocusedSnippet]:
    if not file_text.strip() or not focus_lines:
        return []

    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        snippets = _extract_python_focus_snippets(file_text, focus_lines, max_lines=max_lines)
    else:
        snippets = _extract_generic_focus_snippets(file_text, focus_lines, max_lines=max_lines)

    return _dedupe_snippets(snippets)


def _extract_python_focus_snippets(
    file_text: str,
    focus_lines: list[int],
    *,
    max_lines: int,
) -> list[FocusedSnippet]:
    try:
        module = ast.parse(file_text)
    except SyntaxError:
        return _extract_generic_focus_snippets(file_text, focus_lines, max_lines=max_lines)

    parent_map = _build_parent_map(module)
    snippets: list[FocusedSnippet] = []

    for focus_line in sorted(set(focus_lines)):
        span = _select_python_focus_span(
            file_text,
            module,
            parent_map,
            focus_line,
            max_lines=max_lines,
        )
        if span is None:
            span = _generic_focus_span(file_text, focus_line, max_lines=max_lines)

        start_line, end_line = span
        snippets.append(
            FocusedSnippet(
                start_line=start_line,
                end_line=end_line,
                content=_slice_file_text(file_text, start_line, end_line),
            )
        )

    return snippets


def _select_python_focus_span(
    file_text: str,
    module: ast.AST,
    parent_map: dict[ast.AST, ast.AST],
    focus_line: int,
    *,
    max_lines: int,
) -> tuple[int, int] | None:
    candidates = [
        node
        for node in ast.walk(module)
        if _node_contains_line(node, focus_line)
    ]
    if not candidates:
        return None

    statement_candidates = [
        node
        for node in candidates
        if _is_focus_statement(node)
    ]
    if not statement_candidates:
        return None

    control_blocks = [
        node
        for node in statement_candidates
        if isinstance(node, CONTROL_FLOW_TYPES)
        and _node_line_span(node) <= max_lines
        and _node_line_span(node) > 1
    ]
    if control_blocks:
        chosen = max(
            control_blocks,
            key=lambda node: (_node_line_span(node), -int(getattr(node, "lineno", 0))),
        )
        return int(chosen.lineno), int(chosen.end_lineno)

    base_statement = min(
        statement_candidates,
        key=lambda node: (_node_line_span(node), int(getattr(node, "lineno", 0))),
    )
    span = _expand_statement_neighborhood(base_statement, parent_map, max_lines=max_lines)
    if _needs_balanced_window(span, focus_line):
        return _generic_focus_span(file_text, focus_line, max_lines=max_lines)
    return span


def _expand_statement_neighborhood(
    statement: ast.AST,
    parent_map: dict[ast.AST, ast.AST],
    *,
    max_lines: int,
) -> tuple[int, int]:
    start_line = int(getattr(statement, "lineno", 1))
    end_line = int(getattr(statement, "end_lineno", start_line))
    container, index = _find_statement_container(statement, parent_map)
    if container is None or index is None:
        return start_line, end_line

    selected_indexes = {index}
    distance = 1

    while distance <= len(container):
        added = False
        for candidate_index in (index - distance, index + distance):
            if candidate_index < 0 or candidate_index >= len(container):
                continue
            if candidate_index in selected_indexes:
                continue

            candidate = container[candidate_index]
            candidate_start = int(getattr(candidate, "lineno", start_line))
            candidate_end = int(getattr(candidate, "end_lineno", candidate_start))
            trial_start = min(start_line, candidate_start)
            trial_end = max(end_line, candidate_end)
            if trial_end - trial_start + 1 > max_lines:
                continue

            selected_indexes.add(candidate_index)
            start_line = trial_start
            end_line = trial_end
            added = True

        if not added and index - distance < 0 and index + distance >= len(container):
            break
        distance += 1

    return start_line, end_line


def _find_statement_container(
    statement: ast.AST,
    parent_map: dict[ast.AST, ast.AST],
) -> tuple[list[ast.AST] | None, int | None]:
    current = statement
    while True:
        parent = parent_map.get(current)
        if parent is None:
            return None, None

        for _, value in ast.iter_fields(parent):
            if not isinstance(value, list) or current not in value:
                continue

            statements = [item for item in value if _is_focus_statement(item)]
            if current in statements:
                return statements, statements.index(current)

        current = parent


def _build_parent_map(module: ast.AST) -> dict[ast.AST, ast.AST]:
    parent_map: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(module):
        for child in ast.iter_child_nodes(node):
            parent_map[child] = node
    return parent_map


def _is_focus_statement(node: ast.AST) -> bool:
    if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
        return False
    if isinstance(node, SCOPE_TYPES):
        return False
    return isinstance(node, (ast.stmt, ast.ExceptHandler))


def _node_contains_line(node: ast.AST, line_number: int) -> bool:
    if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
        return False
    return int(node.lineno) <= line_number <= int(node.end_lineno)


def _node_line_span(node: ast.AST) -> int:
    return int(node.end_lineno) - int(node.lineno) + 1


def _extract_generic_focus_snippets(
    file_text: str,
    focus_lines: list[int],
    *,
    max_lines: int,
) -> list[FocusedSnippet]:
    snippets: list[FocusedSnippet] = []
    for focus_line in sorted(set(focus_lines)):
        start_line, end_line = _generic_focus_span(file_text, focus_line, max_lines=max_lines)
        snippets.append(
            FocusedSnippet(
                start_line=start_line,
                end_line=end_line,
                content=_slice_file_text(file_text, start_line, end_line),
            )
        )
    return snippets


def _generic_focus_span(
    file_text: str,
    focus_line: int,
    *,
    max_lines: int,
) -> tuple[int, int]:
    lines = file_text.splitlines()
    if not lines:
        return 1, 1

    before_lines = max(3, ((max_lines - 1) * 3) // 5)
    after_lines = max(2, max_lines - before_lines - 1)

    start_line = max(1, focus_line - before_lines)
    end_line = min(len(lines), focus_line + after_lines)

    while end_line - start_line + 1 < max_lines and start_line > 1:
        start_line -= 1
    while end_line - start_line + 1 < max_lines and end_line < len(lines):
        end_line += 1
    if end_line - start_line + 1 > max_lines:
        end_line = start_line + max_lines - 1
    return start_line, end_line


def _slice_file_text(file_text: str, start_line: int, end_line: int) -> str:
    lines = file_text.splitlines()
    if not lines:
        return ""
    start_index = max(0, start_line - 1)
    end_index = min(len(lines), end_line)
    return "\n".join(lines[start_index:end_index]).rstrip()


def _dedupe_snippets(snippets: list[FocusedSnippet]) -> list[FocusedSnippet]:
    deduped: list[FocusedSnippet] = []
    seen_spans: set[tuple[int, int]] = set()

    for snippet in sorted(snippets, key=lambda item: (item.start_line, item.end_line)):
        key = (snippet.start_line, snippet.end_line)
        if key in seen_spans or not snippet.content.strip():
            continue
        seen_spans.add(key)
        deduped.append(snippet)

    return deduped


def _needs_balanced_window(span: tuple[int, int], focus_line: int) -> bool:
    start_line, end_line = span
    before_context = max(0, focus_line - start_line)
    after_context = max(0, end_line - focus_line)
    return before_context < 2 or after_context < 2
