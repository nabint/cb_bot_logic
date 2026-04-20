from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

GDSCRIPT_DECL_RE = re.compile(r"^(?P<indent>\s*)(?:static\s+)?func\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")
GDSCRIPT_CLASS_RE = re.compile(r"^(?P<indent>\s*)class_name\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")
TOP_LEVEL_ASSIGN_RE = re.compile(r"^(?P<indent>\s*)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=")
C_LIKE_FUNCTION_RE = re.compile(
    r"^\s*(?:static\s+)?(?:inline\s+)?(?:[A-Za-z_][\w\s\*\[\]]+\s+)+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*\{?"
)
C_LIKE_GLOBAL_RE = re.compile(
    r"^\s*(?:static\s+)?(?:const\s+)?(?:[A-Za-z_][\w\s\*\[\]]+\s+)+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*.*)?;"
)


@dataclass(slots=True)
class SymbolSpan:
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    is_top_level: bool

    @property
    def line_span(self) -> int:
        return max(1, self.end_line - self.start_line + 1)


def extract_symbol_spans(path: str | Path, text: str) -> list[SymbolSpan]:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        python_symbols = _extract_python_symbol_spans(text)
        if python_symbols:
            return python_symbols
    if suffix in {".gd", ".gdns", ".gdnlib"}:
        return _extract_gdscript_symbol_spans(text)
    if suffix in {".c", ".h", ".cpp", ".hpp"}:
        return _extract_c_like_symbol_spans(text)
    return _extract_generic_symbol_spans(text)


def find_innermost_symbol(
    symbols: list[SymbolSpan],
    start_line: int,
    end_line: int,
    *,
    allowed_kinds: set[str] | None = None,
) -> SymbolSpan | None:
    candidates: list[SymbolSpan] = []
    for symbol in symbols:
        if allowed_kinds and symbol.kind not in allowed_kinds:
            continue
        if symbol.start_line <= start_line and symbol.end_line >= end_line:
            candidates.append(symbol)

    if not candidates:
        return None

    candidates.sort(key=lambda symbol: (symbol.line_span, symbol.start_line, symbol.qualified_name))
    return candidates[0]


def find_overlapping_symbols(
    symbols: list[SymbolSpan],
    start_line: int,
    end_line: int,
    *,
    allowed_kinds: set[str] | None = None,
) -> list[SymbolSpan]:
    overlaps: list[SymbolSpan] = []
    for symbol in symbols:
        if allowed_kinds and symbol.kind not in allowed_kinds:
            continue
        if symbol.start_line <= end_line and symbol.end_line >= start_line:
            overlaps.append(symbol)

    overlaps.sort(key=lambda symbol: (symbol.start_line, symbol.line_span, symbol.qualified_name))
    return overlaps


def _extract_python_symbol_spans(text: str) -> list[SymbolSpan]:
    try:
        module = ast.parse(text)
    except SyntaxError:
        return []

    symbols: list[SymbolSpan] = []
    _walk_python_body(module.body, symbols, parent_qualified_name=None, parent_kind=None, top_level=True)
    symbols.sort(key=lambda symbol: (symbol.start_line, symbol.end_line, symbol.qualified_name))
    return symbols


def _walk_python_body(
    body: list[ast.stmt],
    symbols: list[SymbolSpan],
    *,
    parent_qualified_name: str | None,
    parent_kind: str | None,
    top_level: bool,
) -> None:
    for node in body:
        if isinstance(node, ast.ClassDef):
            qualified_name = node.name if parent_qualified_name is None else f"{parent_qualified_name}.{node.name}"
            symbols.append(
                SymbolSpan(
                    name=node.name,
                    qualified_name=qualified_name,
                    kind="class",
                    start_line=int(node.lineno),
                    end_line=int(getattr(node, "end_lineno", node.lineno)),
                    is_top_level=top_level,
                )
            )
            _walk_python_body(
                node.body,
                symbols,
                parent_qualified_name=qualified_name,
                parent_kind="class",
                top_level=False,
            )
            continue

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "method" if parent_kind == "class" else "function"
            qualified_name = node.name if parent_qualified_name is None else f"{parent_qualified_name}.{node.name}"
            symbols.append(
                SymbolSpan(
                    name=node.name,
                    qualified_name=qualified_name,
                    kind=kind,
                    start_line=int(node.lineno),
                    end_line=int(getattr(node, "end_lineno", node.lineno)),
                    is_top_level=top_level,
                )
            )
            _walk_python_body(
                node.body,
                symbols,
                parent_qualified_name=qualified_name,
                parent_kind=kind,
                top_level=False,
            )
            continue

        if top_level and isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for target_name in _extract_assignment_target_names(node):
                symbols.append(
                    SymbolSpan(
                        name=target_name,
                        qualified_name=target_name,
                        kind="global",
                        start_line=int(node.lineno),
                        end_line=int(getattr(node, "end_lineno", node.lineno)),
                        is_top_level=True,
                    )
                )


def _extract_assignment_target_names(node: ast.stmt) -> list[str]:
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets.extend(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets.append(node.target)
    elif isinstance(node, ast.AugAssign):
        targets.append(node.target)

    names: list[str] = []
    for target in targets:
        names.extend(_flatten_assignment_names(target))
    return names


def _flatten_assignment_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in target.elts:
            names.extend(_flatten_assignment_names(element))
        return names
    return []


def _extract_gdscript_symbol_spans(text: str) -> list[SymbolSpan]:
    lines = text.splitlines()
    symbols: list[tuple[int, int, str, str]] = []

    for line_number, line in enumerate(lines, start=1):
        class_match = GDSCRIPT_CLASS_RE.match(line)
        if class_match:
            symbols.append((line_number, len(class_match.group("indent")), class_match.group("name"), "class"))
            continue

        decl_match = GDSCRIPT_DECL_RE.match(line)
        if decl_match:
            symbols.append((line_number, len(decl_match.group("indent")), decl_match.group("name"), "function"))
            continue

        assign_match = TOP_LEVEL_ASSIGN_RE.match(line)
        if assign_match and len(assign_match.group("indent")) == 0:
            symbols.append((line_number, 0, assign_match.group("name"), "global"))

    return _line_symbols_to_spans(symbols, total_lines=len(lines))


def _extract_c_like_symbol_spans(text: str) -> list[SymbolSpan]:
    lines = text.splitlines()
    symbols: list[SymbolSpan] = []
    total_lines = len(lines)
    line_number = 1

    while line_number <= total_lines:
        line = lines[line_number - 1]
        function_match = C_LIKE_FUNCTION_RE.match(line)
        if function_match and not line.strip().endswith(";"):
            end_line = _find_c_block_end(lines, line_number)
            name = function_match.group("name")
            symbols.append(
                SymbolSpan(
                    name=name,
                    qualified_name=name,
                    kind="function",
                    start_line=line_number,
                    end_line=end_line,
                    is_top_level=True,
                )
            )
            line_number = max(line_number + 1, end_line)
            continue

        global_match = C_LIKE_GLOBAL_RE.match(line)
        if global_match:
            name = global_match.group("name")
            symbols.append(
                SymbolSpan(
                    name=name,
                    qualified_name=name,
                    kind="global",
                    start_line=line_number,
                    end_line=line_number,
                    is_top_level=True,
                )
            )

        line_number += 1

    return symbols


def _find_c_block_end(lines: list[str], start_line: int) -> int:
    brace_depth = 0
    seen_opening_brace = False
    for line_number in range(start_line, len(lines) + 1):
        line = lines[line_number - 1]
        brace_depth += line.count("{")
        if line.count("{") > 0:
            seen_opening_brace = True
        brace_depth -= line.count("}")
        if seen_opening_brace and brace_depth <= 0:
            return line_number
    return len(lines)


def _extract_generic_symbol_spans(text: str) -> list[SymbolSpan]:
    lines = text.splitlines()
    raw_symbols: list[tuple[int, int, str, str]] = []

    for line_number, line in enumerate(lines, start=1):
        assign_match = TOP_LEVEL_ASSIGN_RE.match(line)
        if assign_match and len(assign_match.group("indent")) == 0:
            raw_symbols.append((line_number, 0, assign_match.group("name"), "global"))

    return _line_symbols_to_spans(raw_symbols, total_lines=len(lines))


def _line_symbols_to_spans(
    symbols: list[tuple[int, int, str, str]],
    *,
    total_lines: int,
) -> list[SymbolSpan]:
    spans: list[SymbolSpan] = []
    for index, (start_line, indent, name, kind) in enumerate(symbols):
        if kind == "global":
            end_line = start_line
        else:
            end_line = total_lines
            for next_start, next_indent, _, _ in symbols[index + 1:]:
                if next_indent <= indent:
                    end_line = next_start - 1
                    break

        qualified_name = name
        spans.append(
            SymbolSpan(
                name=name,
                qualified_name=qualified_name,
                kind=kind,
                start_line=start_line,
                end_line=max(start_line, end_line),
                is_top_level=True,
            )
        )

    return spans
