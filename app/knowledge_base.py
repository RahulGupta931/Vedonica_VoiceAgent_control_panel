"""
Lightweight knowledge base for product info, policies, T&Cs, About Us, FAQ.

Why this exists
----------------
All of this content used to live inline in VEDONICA_SYSTEM_PROMPT, which
means it was sent to the LLM on EVERY turn of EVERY call — you're paying
for the full product catalog + policies + FAQ text over and over, all call
long, even when the customer never asks about any of it.

The fix: this content lives here instead, and the LLM pulls in only the
specific ~30-80 words it actually needs via the `search_knowledge_base` tool
(see app/tools.py), only on the turn where it's relevant. Token cost drops
from "whole catalog, every turn" to "one small snippet, on demand" — and it
stays flat as you add more products/FAQ, instead of growing your prompt
(and your per-call cost) every time you add content.

Why keyword search, not embeddings
-----------------------------------
For a catalog this size (a couple of products, a handful of policies, a
short FAQ), a plain keyword-overlap scorer is faster, adds zero infra (no
vector DB, no embedding API call, no extra latency on a live call — that
matters a lot for voice), and is trivial to read/debug. If the catalog grows
into the hundreds of items, swap `_score()` for a real vector-search call;
the tool interface (`search`) doesn't need to change, so nothing else in the
codebase would need touching.

Content itself lives in app/knowledge_data.py as plain Python data, so
whoever manages the catalog/FAQ can edit it without touching prompt or
pipeline code.
"""

import re
from dataclasses import dataclass, field

from app.knowledge_data import ABOUT_US, FAQS, POLICIES, PRODUCTS

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")

# Common filler words that shouldn't count toward a match — otherwise a
# query like "what is the price" would weakly match almost everything.
_STOPWORDS = {
    "the", "is", "are", "a", "an", "of", "for", "to", "and", "or", "what",
    "how", "do", "does", "can", "i", "my", "you", "your", "please", "tell",
    "me", "about", "kya", "hai", "ka", "ke", "ki", "kaise", "mujhe", "aap",
}


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)} - _STOPWORDS


@dataclass
class _Entry:
    id: str
    category: str  # "about" | "product" | "policy" | "faq"
    title: str
    content: str
    keywords: set[str] = field(default_factory=set)


def _build_index() -> list[_Entry]:
    entries: list[_Entry] = []

    entries.append(
        _Entry(
            id="about_us",
            category="about",
            title="About Vedonica",
            content=ABOUT_US,
            keywords=_tokenize("about us company vedonica " + ABOUT_US),
        )
    )

    for p in PRODUCTS:
        content = (
            f"{p['name']}. Price: {p['price']}. "
            f"Benefits: {', '.join(p['benefits'])}."
        )
        if p.get("ingredients"):
            content += f" Ingredients: {', '.join(p['ingredients'])}."
        if p.get("other"):
            content += f" {p['other']}."
        entries.append(
            _Entry(
                id=f"product_{p['id']}",
                category="product",
                title=p["name"],
                content=content,
                keywords=_tokenize(
                    p["name"] + " " + " ".join(p.get("aliases", [])) + " " + content
                ),
            )
        )

    for topic_key, text in POLICIES.items():
        title = topic_key.replace("_", " ").title()
        entries.append(
            _Entry(
                id=f"policy_{topic_key}",
                category="policy",
                title=title,
                content=text,
                keywords=_tokenize(topic_key.replace("_", " ") + " policy " + text),
            )
        )

    for i, faq in enumerate(FAQS):
        entries.append(
            _Entry(
                id=f"faq_{i}",
                category="faq",
                title=faq["question"],
                content=faq["answer"],
                keywords=_tokenize(faq["question"] + " " + faq["answer"]),
            )
        )

    return entries


_INDEX: list[_Entry] = _build_index()


def search(query: str, top_k: int = 2) -> list[dict]:
    """Return up to top_k knowledge entries most relevant to `query`,
    ranked by keyword overlap. Empty list if nothing matches at all."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    scored = []
    for entry in _INDEX:
        overlap = query_tokens & entry.keywords
        if overlap:
            scored.append((len(overlap), entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    return [
        {"topic": e.title, "category": e.category, "info": e.content}
        for _, e in scored[:top_k]
    ]


def list_product_names() -> list[str]:
    return [p["name"] for p in PRODUCTS]
