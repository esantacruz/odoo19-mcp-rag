from dataclasses import replace

from src.parsing import Chunker, document_chunks, expand_includes, parse_sections


def test_rst_preserves_code_inside_autodoc_and_nested_notes():
    text = """ORM
===

.. autoclass:: Model

    .. attribute:: domain_force

        Domain restricts record visibility.

    .. code-block:: python

        @api.depends("price")
        def _compute_total(self):
            self.total = self.price * 2

XML
---

Literal example::

    <field name="domain_force">[("user_id", "=", user.id)]</field>
"""
    sections = parse_sections(text, "fallback")
    full = "\n".join(s.text for s in sections)
    assert '@api.depends("price")' in full
    assert "```python" in full
    assert '```text\n<field name="domain_force">' in full
    assert "Domain restricts record visibility." in full
    assert sections[-1].headings[-1] == "XML"


def test_chunk_budget_preserves_code_and_unicode(settings):
    chunker = Chunker(replace(settings, chunk_size=128, chunk_overlap=24))
    code = "```python\n" + "".join(f"field_{i} = 'á漢字'\n" for i in range(150)) + "```\n"
    chunks = chunker.split(code)
    assert len(chunks) > 2
    assert all(chunker.count(chunk) <= 128 for chunk in chunks)
    assert all(chunk.startswith("```python\n") and chunk.endswith("```") for chunk in chunks)
    assert all(f"field_{i}" in "\n".join(chunks) for i in range(150))
    assert "\ufffd" not in "".join(chunks)


def test_includes_cannot_escape_root_and_change_parent_hash(tmp_path, settings):
    root = tmp_path / "content"
    root.mkdir()
    parent = root / "page.rst"
    include = root / "part.rst"
    include.write_text("Included original text", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("SHOULD NOT APPEAR", encoding="utf-8")
    parent.write_text("Title\n=====\n\n.. include:: part.rst\n\n.. include:: ../secret.txt\n")
    expanded = expand_includes(parent, root)
    assert "Included original text" in expanded
    assert "SHOULD NOT APPEAR" not in expanded
    first, _ = document_chunks(parent, root, Chunker(settings), "abc")
    include.write_text("Included changed text", encoding="utf-8")
    second, _ = document_chunks(parent, root, Chunker(settings), "abc")
    assert first != second


def test_fenced_markdown_does_not_create_false_headings():
    sections = parse_sections(
        "# Actual title\n\n```python\n# code comment\nx = 1\n```\n", "x", True
    )
    assert len(sections) == 1
    assert sections[0].headings == ("Actual title",)
    assert "# code comment" in sections[0].text


def test_long_prose_has_overlap(settings):
    chunker = Chunker(settings)
    text = " ".join(f"word{i}" for i in range(500))
    chunks = chunker.split(text)
    assert len(chunks) > 2
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert previous.split()[-1] in current.split()[:20]
        assert chunker.count(current) <= settings.chunk_size
