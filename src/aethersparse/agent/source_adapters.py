"""Bounded production source adapters and deterministic source-impact graphing."""

from __future__ import annotations

import ast
import hashlib
import re
from collections import defaultdict
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .knowledge import CanonicalSourceObject, SourceProvenance, SourceType


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript"}:
            self._suppressed += 1
        elif tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "pre", "code"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self._suppressed = max(0, self._suppressed - 1)
        elif tag in {"p", "li", "h1", "h2", "h3", "h4", "pre"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppressed == 0:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"[ \t]+", " ", "".join(self.parts)).strip()


class FileSourceAdapter:
    """Compiles Markdown, HTML, manuals, specs, and project documentation."""

    def __init__(
        self,
        root: Path,
        *,
        source_namespace: str,
        source_type: SourceType,
        source_version: str,
        license_id: str,
        patterns: tuple[str, ...],
    ) -> None:
        self.root = root.resolve()
        self.source_namespace = source_namespace
        self.source_type = source_type
        self.source_version = source_version
        self.license_id = license_id
        self.patterns = patterns

    def iter_objects(self) -> Iterator[CanonicalSourceObject]:
        paths: set[Path] = set()
        for pattern in self.patterns:
            paths.update(path for path in self.root.glob(pattern) if path.is_file())
        for path in sorted(paths):
            resolved = path.resolve()
            resolved.relative_to(self.root)
            data = resolved.read_bytes()
            if len(data) > 4 * 1024 * 1024:
                raise ValueError(f"source object exceeds 4 MiB bound: {resolved}")
            text = data.decode("utf-8", errors="replace")
            if resolved.suffix.casefold() in {".html", ".htm"}:
                parser = _VisibleTextParser()
                parser.feed(text)
                text = parser.text()
            relative = resolved.relative_to(self.root).as_posix()
            yield CanonicalSourceObject(
                canonical_object_id=f"{self.source_namespace}:{relative}",
                source_namespace=self.source_namespace,
                source_type=self.source_type,
                source_version=self.source_version,
                title=_title(text, relative),
                body=text,
                provenance=SourceProvenance(
                    license_id=self.license_id,
                    origin=relative,
                    revision=self.source_version,
                    content_digest=_digest(data),
                ),
            )


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:256]
    return fallback


class SourceSymbol(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbol_id: str
    path: str
    name: str
    kind: str
    references: tuple[str, ...] = ()


class SourceImpactGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbols: tuple[SourceSymbol, ...]
    path_dependencies: tuple[tuple[str, str], ...]
    test_mapping: tuple[tuple[str, str], ...]

    def references_to(self, name: str) -> tuple[SourceSymbol, ...]:
        return tuple(item for item in self.symbols if name in item.references)

    def affected_files(self, name: str) -> tuple[str, ...]:
        owners = {item.path for item in self.symbols if item.name == name}
        references = {item.path for item in self.references_to(name)}
        return tuple(sorted(owners | references))


def build_python_impact_graph(root: Path) -> SourceImpactGraph:
    """Extract definitions, references, imports, and deterministic test mapping."""

    resolved_root = root.resolve()
    symbols: list[SourceSymbol] = []
    dependencies: set[tuple[str, str]] = set()
    module_paths: dict[str, str] = {}
    parsed: dict[str, ast.AST] = {}
    for path in sorted(resolved_root.rglob("*.py")):
        if any(part in {".git", ".venv", "build", "dist"} for part in path.parts):
            continue
        relative = path.relative_to(resolved_root).as_posix()
        module = relative.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
        module_paths[module] = relative
        parsed[relative] = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    for relative, tree in parsed.items():
        references = tuple(
            sorted(
                {
                    node.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                }
            )
        )
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append(
                    SourceSymbol(
                        symbol_id=f"{relative}:{node.lineno}:{node.name}",
                        path=relative,
                        name=node.name,
                        kind=type(node).__name__,
                        references=references,
                    )
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in module_paths:
                        dependencies.add((relative, module_paths[alias.name]))
            elif isinstance(node, ast.ImportFrom) and node.module in module_paths:
                dependencies.add((relative, module_paths[node.module]))
    source_by_stem: dict[str, list[str]] = defaultdict(list)
    tests: list[str] = []
    for relative in parsed:
        stem = Path(relative).stem.removeprefix("test_")
        if Path(relative).stem.startswith("test_") or relative.startswith("tests/"):
            tests.append(relative)
        else:
            source_by_stem[stem].append(relative)
    mappings = {
        (source, test)
        for test in tests
        for source in source_by_stem.get(Path(test).stem.removeprefix("test_"), [])
    }
    return SourceImpactGraph(
        symbols=tuple(sorted(symbols, key=lambda item: item.symbol_id)),
        path_dependencies=tuple(sorted(dependencies)),
        test_mapping=tuple(sorted(mappings)),
    )
