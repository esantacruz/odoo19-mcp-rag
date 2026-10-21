"""Conservative RST/Markdown reader. Never execute Sphinx directives or imported code."""

from __future__ import annotations

import hashlib
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

import tiktoken

from src.config import Settings

DIRECTIVE = re.compile(r"^\s*\.\.\s+([\w:-]+)::\s*(.*)$")
ROLE = re.compile(r":[\w:]+:`([^`]+)`")
UNDERLINE = re.compile(r"^([=\-~^\"'`:+*#<>_])\1{2,}\s*$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")


@dataclass(frozen=True)
class Section:
    headings: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    metadata: dict[str, str | int]


def clean_inline(text: str) -> str:
    # Preserve identifier literals and link targets; remove only Sphinx role syntax.
    text = ROLE.sub(lambda m: m.group(1), text)
    return re.sub(r"`([^`<>]+)\s*<(https?://[^>]+)>`_?", r"[\1](\2)", text)


def expand_includes(path: Path, content_root: Path, seen: tuple[Path, ...] = ()) -> str:
    """Expand trusted local text only; paths must remain inside content_root."""
    path = path.resolve()
    if not path.is_relative_to(content_root.resolve()):
        raise ValueError("Source paths must remain inside the documentation content root")
    if path in seen or len(seen) >= 12:
        return f"[Include cycle/depth limit: {path.name}]"
    lines = path.read_text(encoding="utf-8").splitlines()
    output, i = [], 0
    while i < len(lines):
        match = DIRECTIVE.match(lines[i])
        if not match or match.group(1) not in {"include", "literalinclude"}:
            output.append(lines[i])
            i += 1
            continue
        name, target = match.groups()
        indent = len(lines[i]) - len(lines[i].lstrip())
        options = []
        i += 1
        while i < len(lines) and (
            not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent
        ):
            options.append(lines[i].strip())
            i += 1
        candidate = (
            content_root / target.lstrip("/") if target.startswith("/") else path.parent / target
        ).resolve()
        if (
            candidate.is_relative_to(content_root.resolve())
            and candidate.is_file()
            and candidate.suffix in {".rst", ".md", ".txt", ".py", ".xml", ".js"}
        ):
            if name == "include":
                body = expand_includes(candidate, content_root, (*seen, path))
            else:
                language = next(
                    (
                        x.split(":language:", 1)[1].strip()
                        for x in options
                        if x.startswith(":language:")
                    ),
                    "text",
                )
                body = f".. code-block:: {language}\n\n" + textwrap.indent(
                    candidate.read_text(encoding="utf-8"), "   "
                )
            # Full includes are retained. Selected-line include options are not evaluated.
            output.extend(" " * indent + line for line in body.splitlines())
        else:
            output.append(" " * indent + f"[External/unavailable {name}: {target}]")
    return "\n".join(output)


def parse_sections(text: str, fallback: str, markdown: bool = False) -> list[Section]:
    lines = text.splitlines()
    sections: list[Section] = []
    headings: list[str] = []
    styles: dict[str, int] = {}
    body: list[str] = []

    def flush() -> None:
        value = "\n".join(body).strip()
        if value:
            sections.append(Section(tuple(headings or [fallback]), value))
        body.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        fence = FENCE.match(line)
        if fence:
            delimiter = fence.group(1)
            body.append(line)
            i += 1
            while i < len(lines):
                body.append(lines[i])
                is_end = lines[i].strip().startswith(delimiter)
                i += 1
                if is_end:
                    break
            continue
        heading, level, consumed = None, 0, 1
        if markdown and (match := re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)):
            level, heading = len(match.group(1)) - 1, match.group(2)
        elif (
            line
            and not line[0].isspace()
            and i + 1 < len(lines)
            and UNDERLINE.match(lines[i + 1])
            and not UNDERLINE.match(line)
        ):
            marker = lines[i + 1][0]
            level = styles.setdefault(marker, len(styles))
            heading, consumed = line, 2
        if heading:
            flush()
            headings[:] = headings[:level] + [clean_inline(heading)]
            i += consumed
            continue
        if UNDERLINE.match(line):
            i += 1
            continue
        directive = DIRECTIVE.match(line)
        if directive:
            kind, argument = directive.groups()
            indent = len(line) - len(line.lstrip())
            inner: list[str] = []
            i += 1
            while i < len(lines) and (
                not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent
            ):
                inner.append(lines[i])
                i += 1
            if kind in {"code-block", "code", "sourcecode", "testcode"}:
                # Only leading options are removed. :name: inside code is actual code.
                while inner and (not inner[0].strip() or inner[0].strip().startswith(":")):
                    inner.pop(0)
                code = textwrap.dedent("\n".join(inner)).rstrip()
                fence_chars = "`" * max(
                    3, max((len(m.group()) + 1 for m in re.finditer(r"`+", code)), default=3)
                )
                body.extend(["", f"{fence_chars}{argument or 'text'}", code, fence_chars, ""])
            elif kind not in {"image", "figure", "toctree", "contents", "meta", "index"}:
                if kind.startswith("auto"):
                    body.append(
                        f"[Sphinx {kind}: {argument}; "
                        "generated API content is not in this RST source.]"
                    )
                    nested = parse_sections(textwrap.dedent("\n".join(inner)), fallback, markdown)
                    body.extend(s.text for s in nested)
                else:
                    if argument:
                        body.append(clean_inline(f"{kind}: {argument}"))
                    nested = parse_sections(textwrap.dedent("\n".join(inner)), fallback, markdown)
                    body.extend(s.text for s in nested)
            continue
        # Targets, comments and directive options carry no useful prose.
        if re.match(r"^\s*\.\.\s", line) or re.match(r"^\s*:[\w-]+:(?:\s|$)", line):
            i += 1
            continue
        if not markdown and line.rstrip().endswith("::"):
            # RST literal paragraphs, including XML snippets without code-block directives.
            body.append(clean_inline(line.rstrip()[:-1]))
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            base = len(line) - len(line.lstrip())
            if j < len(lines) and len(lines[j]) - len(lines[j].lstrip()) > base:
                literal = []
                while j < len(lines) and (
                    not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > base
                ):
                    literal.append(lines[j])
                    j += 1
                body.extend(
                    ["", "```text", textwrap.dedent("\n".join(literal)).rstrip(), "```", ""]
                )
                i = j
                continue
            i += 1
            continue
        body.append(clean_inline(line))
        i += 1
    flush()
    return sections


class Chunker:
    def __init__(self, settings: Settings):
        self.size = settings.chunk_size
        self.overlap = settings.chunk_overlap
        self.encoding = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        return len(self.encoding.encode(text, disallowed_special=()))

    def _text_parts(self, text: str, budget: int) -> list[str]:
        """Split by characters at a token limit, never through UTF-8 token boundaries."""
        parts = []
        while text:
            if self.count(text) <= budget:
                parts.append(text)
                break
            lo, hi = 1, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self.count(text[:mid]) <= budget:
                    lo = mid
                else:
                    hi = mid - 1
            cut = text.rfind("\n", 0, lo + 1)
            if cut < lo // 2:
                cut = text.rfind(" ", 0, lo + 1)
            cut = cut + 1 if cut >= lo // 2 else lo
            parts.append(text[:cut])
            text = text[cut:]
        return parts

    def _units(self, text: str) -> list[str]:
        # Whole code blocks are indivisible unless larger than the entire chunk budget.
        raw, buffer, in_code, delimiter = [], [], False, ""
        for line in text.splitlines(keepends=True):
            fence = FENCE.match(line)
            if fence and not in_code:
                if buffer:
                    raw.append("".join(buffer))
                    buffer = []
                in_code, delimiter = True, fence.group(1)
            buffer.append(line)
            if in_code and fence and line.strip() == delimiter and len(buffer) > 1:
                raw.append("".join(buffer))
                buffer, in_code = [], False
            elif not in_code and not line.strip():
                raw.append("".join(buffer))
                buffer = []
        if buffer:
            raw.append("".join(buffer))
        result = []
        for block in raw:
            lines = block.splitlines()
            code_block = bool(lines and FENCE.match(lines[0]))
            block_budget = self.size if code_block else self.size - self.overlap
            if self.count(block) <= block_budget:
                result.append(block)
                continue
            if code_block and len(lines) >= 2:
                block_delimiter = FENCE.match(lines[0]).group(1)
                prefix, suffix = lines[0] + "\n", "\n" + block_delimiter + "\n"
                budget = self.size - self.count(prefix + suffix) - 4
                result.extend(
                    prefix + part.rstrip("\n") + suffix
                    for part in self._text_parts("\n".join(lines[1:-1]), budget)
                )
            else:
                result.extend(self._text_parts(block, block_budget))
        return result

    def _tail(self, text: str, budget: int) -> str:
        if budget <= 0:
            return ""
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.count(text[mid:]) <= budget:
                hi = mid
            else:
                lo = mid + 1
        suffix = text[lo:]
        # Prefer a whole word at the overlap boundary.
        boundary = suffix.find(" ")
        return suffix[boundary + 1 :] if boundary >= 0 else suffix

    def split(self, text: str) -> list[str]:
        result, current = [], []
        for unit in self._units(text):
            if current and self.count("".join(current) + unit) > self.size:
                result.append("".join(current).strip())
                tail = []
                if self.overlap:
                    for previous in reversed(current):
                        if self.count(previous + "".join(tail)) > self.overlap:
                            if not FENCE.match(previous.splitlines()[0] if previous else ""):
                                remaining = self.overlap - self.count("".join(tail))
                                tail.insert(0, self._tail(previous, remaining))
                            break
                        tail.insert(0, previous)
                current = tail
                while current and self.count("".join(current) + unit) > self.size:
                    current.pop(0)
            current.append(unit)
        if current and "".join(current).strip():
            result.append("".join(current).strip())
        return [part for part in result if part]


def category_for(relative: Path) -> str:
    parts = relative.parts
    if parts[0] != "applications":
        return parts[0] if len(parts) > 1 else "general"
    for category in (
        "accounting",
        "inventory",
        "manufacturing",
        "purchase",
        "sales",
        "crm",
        "studio",
        "payroll",
        "documents",
        "sign",
        "helpdesk",
        "project",
    ):
        if category in parts or category == relative.stem:
            return category
    return parts[2] if len(parts) > 3 else (parts[1] if len(parts) > 2 else "applications")


def document_chunks(
    path: Path, root: Path, chunker: Chunker, commit: str
) -> tuple[str, list[Chunk]]:
    relative = path.relative_to(root)
    source = expand_includes(path, root)
    digest = hashlib.sha256(source.encode()).hexdigest()
    chunks = []
    for section in parse_sections(source, path.stem.replace("_", " "), path.suffix == ".md"):
        heading = " > ".join(section.headings)
        for part in chunker.split(section.text):
            index = len(chunks)
            identity = f"{relative.as_posix()}\0{index}\0{heading}\0{part}"
            chunks.append(
                Chunk(
                    hashlib.sha256(identity.encode()).hexdigest(),
                    part,
                    {
                        "file_path": "content/" + relative.as_posix(),
                        "url": "https://www.odoo.com/documentation/19.0/"
                        + relative.with_suffix(".html").as_posix(),
                        "source_url": f"https://github.com/odoo/documentation/blob/{commit}/content/{relative.as_posix()}",
                        "title": section.headings[0],
                        "headings": heading,
                        "category": category_for(relative),
                        "area": relative.parts[0],
                        "language": "en",
                        "version": "19.0",
                        "edition": "unspecified",
                        "chunk_index": index,
                        "tokens": chunker.count(part),
                        "source_commit": commit,
                        "source_hash": digest,
                    },
                )
            )
    return digest, chunks
