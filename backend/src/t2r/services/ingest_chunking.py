from __future__ import annotations


def chunk_markdown(text: str, budget: int = 6000) -> list[str]:
    """Split a markdown document into chunks of at most ~``budget`` chars,
    cutting only at top-level (``#``/``##``) section boundaries so a recipe or
    rule is never sliced mid-block.

    Why: the glossary/SQL-notes ingest sends each chunk to the LLM and expects a
    JSON reply; a single huge call overflowed the model's output token budget and
    returned truncated, unparseable JSON. Per-section chunking keeps every reply
    small. A section larger than the budget becomes its own chunk; a document with
    no headers at all is hard-split as a last resort.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= budget:
        return [text]

    # Group lines into header-delimited sections (header line starts its section).
    sections: list[list[str]] = []
    cur: list[str] = []
    for ln in text.split("\n"):
        if ln.lstrip().startswith("#") and cur:
            sections.append(cur)
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        sections.append(cur)

    # Greedily pack sections into <=budget chunks.
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for sec in sections:
        sec_text = "\n".join(sec)
        if buf and buf_len + len(sec_text) + 1 > budget:
            chunks.append("\n".join(buf))
            buf, buf_len = [], 0
        buf.append(sec_text)
        buf_len += len(sec_text) + 1
    if buf:
        chunks.append("\n".join(buf))

    # Last resort: a chunk with no internal headers can still exceed the budget
    # (one giant section / header-less doc) — hard-split it by char budget.
    out: list[str] = []
    for c in chunks:
        if len(c) <= int(budget * 1.5):
            out.append(c)
        else:
            out.extend(c[i : i + budget] for i in range(0, len(c), budget))
    return out
