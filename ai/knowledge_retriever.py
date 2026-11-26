"""Retrieval over a small, curated, local knowledge base.

Only documentation that describes what the application actually does is
indexed. That matters: the repository README also contains "Planned" sections,
and grounding answers in those would make the assistant claim features that do
not exist. The corpus is therefore a hand-verified facts file.

No user data is ever indexed here. Owner-scoped metadata is assembled by the
route layer and passed to the prompt separately.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
MAX_CHUNK_CHARS = 1100
MAX_PROMPT_CHARS = 3600

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_WORD_RE = re.compile(r"[a-z0-9]{3,}")

STOPWORDS = {
    "the", "and", "for", "you", "your", "can", "how", "what", "why", "when",
    "where", "who", "does", "did", "are", "was", "were", "with", "this",
    "that", "these", "those", "from", "have", "has", "had", "not", "but",
    "into", "about", "there", "here", "they", "them", "then", "than", "will",
    "would", "could", "should", "please", "tell", "give", "make", "want",
    "need", "know", "explain", "some", "any", "its", "it's", "use", "using",
    "used", "one", "two", "get", "got", "like", "just", "also", "more",
}


@dataclass(frozen=True)
class Chunk:
    source: str
    title: str
    text: str
    tokens: frozenset


# Generic asks ("how do I use this app", "how does it work") are made almost
# entirely of stopwords, so plain token overlap finds nothing. These patterns
# add the topic words the corpus actually uses. They only widen the search; the
# answer is still generated from the retrieved text.
_QUERY_HINTS = (
    (
        r"\b(get started|getting started|first time|new here|start using|use (this|the) app|"
        r"(this|the) app works?|how (do|can|should) i use|what is (this|the) app|"
        r"overview|walk ?through|what does (this|the) app do)\b",
        "getting started dashboard files upload notes contacts sharing settings trash",
    ),
    (
        r"\b(can (you|it) do|capabilit(?:y|ies)|what can i ask|commands|help me)\b",
        "voice assistant capabilities",
    ),
    (
        r"\b(sign ?in|log ?in|register|create an account|sign ?up)\b",
        "accounts authentication",
    ),
)

_HINT_RES = tuple((re.compile(pattern, re.IGNORECASE), terms) for pattern, terms in _QUERY_HINTS)

# A section whose title names the topic is a better answer than a broad overview
# that merely mentions the same words.
TITLE_BOOST = 2.0

_cache: dict = {"signature": None, "chunks": ()}


def _tokenize(text: str) -> list[str]:
    return [word for word in _WORD_RE.findall((text or "").lower()) if word not in STOPWORDS]


def _split_markdown(source: str, content: str) -> list[Chunk]:
    """Split on headings; a long section is cut on paragraph boundaries."""
    chunks: list[Chunk] = []
    title = source
    buffer: list[str] = []

    def flush():
        body = "\n".join(buffer).strip()
        if body:
            for piece in _pack(body):
                chunks.append(
                    Chunk(
                        source=source,
                        title=title,
                        text=piece,
                        tokens=frozenset(_tokenize(title + " " + piece)),
                    )
                )

    for line in content.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            buffer = []
            title = heading.group(2).strip()
            continue
        buffer.append(line)
    flush()
    return chunks


def _pack(body: str) -> list[str]:
    if len(body) <= MAX_CHUNK_CHARS:
        return [body]
    pieces, current = [], ""
    for paragraph in re.split(r"\n\s*\n", body):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 2 > MAX_CHUNK_CHARS:
            pieces.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip() if current else paragraph
    if current:
        pieces.append(current)
    return pieces


def load_corpus() -> tuple[Chunk, ...]:
    """Load and cache the corpus, rebuilding when a file changes."""
    files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    signature = tuple(
        (path.name, path.stat().st_mtime_ns, path.stat().st_size) for path in files
    )
    if _cache["signature"] == signature and _cache["chunks"]:
        return _cache["chunks"]
    chunks: list[Chunk] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        chunks.extend(_split_markdown(path.stem, content))
    _cache["signature"] = signature
    _cache["chunks"] = tuple(chunks)
    return _cache["chunks"]


def retrieve(query: str, top_k: int = 5, min_score: float = 1.2) -> list[Chunk]:
    """Best-matching documentation chunks for a question.

    A light IDF weighting keeps rare terms (``trash``, ``csrf``) ahead of words
    that appear in every section. Ties keep file order so results are stable.
    """
    terms = set(_tokenize(query))
    for pattern, expansion in _HINT_RES:
        if pattern.search(query or ""):
            terms.update(_tokenize(expansion))
    if not terms:
        return []
    chunks = load_corpus()
    if not chunks:
        return []
    total = len(chunks)
    document_frequency = {}
    for term in terms:
        document_frequency[term] = sum(1 for chunk in chunks if term in chunk.tokens)

    scored = []
    for chunk in chunks:
        title_terms = set(_tokenize(chunk.title))
        score = 0.0
        for term in terms:
            if term in chunk.tokens:
                weight = math.log(1 + total / (1 + document_frequency[term]))
                score += weight * (TITLE_BOOST if term in title_terms else 1.0)
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for score, chunk in scored[:top_k] if score >= min_score]


def format_for_prompt(chunks: list[Chunk], limit: int = MAX_PROMPT_CHARS) -> str:
    """Render retrieved chunks for the prompt, respecting a size budget."""
    parts, used = [], 0
    for chunk in chunks:
        block = f"[{chunk.source} — {chunk.title}]\n{chunk.text}"
        if used + len(block) > limit:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def has_documentation() -> bool:
    return bool(load_corpus())
