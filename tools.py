"""
tools.py

The three required FitFindr tools (+ a small parse_query helper). Each tool is a
standalone function that can be called and tested on its own before being wired
into the agent loop.

Tools:
    search_listings(description, size, max_price)  -> list[dict]
    suggest_outfit(new_item, wardrobe)              -> str
    create_fit_card(outfit, new_item)               -> str
    parse_query(query)                              -> dict   (helper)
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

MODEL = "llama-3.3-70b-versatile"

# words that show up in every query but don't tell us anything about the item,
# so I don't want them inflating the relevance score
STOPWORDS = {
    "a", "an", "the", "i", "im", "for", "looking", "want", "need", "some",
    "under", "over", "below", "size", "in", "of", "and", "to", "with",
    "my", "me", "is", "are", "that", "this", "something", "find", "show",
}


# -- Groq client ---------------------------------------------------------------

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _chat(prompt: str, temperature: float = 0.7, max_tokens: int = 400) -> str:
    """Tiny wrapper so I'm not repeating the same chat.completions call 3 times."""
    client = _get_groq_client()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


# -- helper: parse_query -------------------------------------------------------

def parse_query(query: str) -> dict:
    """
    Pull a description, size, and max_price out of a natural language query.

    Pulled this out of the agent so the planning loop stays readable. It's just
    regex — nothing fancy. Returns {"description", "size", "max_price"}.
    """
    text = query.strip()

    # price: "under $30", "$30", "30 dollars", "max 30"
    max_price = None
    price_match = re.search(
        r"(?:under|below|less than|max|up to|<)?\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:dollars|bucks|\$)?",
        text,
        re.IGNORECASE,
    )
    # only trust the number if the query actually talks about price/money,
    # otherwise a stray number (like a shoe size) gets read as a price
    if price_match and re.search(r"\$|under|below|less than|dollar|buck|price|max|up to", text, re.IGNORECASE):
        max_price = float(price_match.group(1))

    # size: "size M", "size 8", or a standalone XS/S/M/L/XL token
    size = None
    size_match = re.search(r"size\s+([a-z0-9.]+)", text, re.IGNORECASE)
    if size_match:
        size = size_match.group(1).upper()
    else:
        token = re.search(r"\b(XXS|XS|S|M|L|XL|XXL)\b", text)  # case-sensitive on purpose
        if token:
            size = token.group(1).upper()

    # description = the query with the price/size noise stripped out so it reads cleanly
    description = text
    description = re.sub(r"(?:under|below|less than|max|up to|<)?\s*\$\s*\d+(?:\.\d+)?", "", description, flags=re.IGNORECASE)
    description = re.sub(r"\bunder\s+\d+(?:\.\d+)?\b", "", description, flags=re.IGNORECASE)
    description = re.sub(r"\bsize\s+[a-z0-9.]+", "", description, flags=re.IGNORECASE)
    description = re.sub(r"\s+", " ", description).strip()
    if not description:
        description = text  # fell through to nothing, just use the raw query

    return {"description": description, "size": size, "max_price": max_price}


# -- Tool 1: search_listings ---------------------------------------------------

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Returns a list of matching listing dicts sorted by relevance (best first),
    or [] if nothing matches. Never raises.
    """
    listings = load_listings()

    # keywords from the description, minus the filler words
    keywords = [
        w for w in re.findall(r"[a-z0-9]+", description.lower())
        if w not in STOPWORDS and len(w) > 1
    ]

    scored = []
    for item in listings:
        # filter: price ceiling
        if max_price is not None and item["price"] > max_price:
            continue

        # filter: size (case-insensitive substring, so "M" matches "S/M")
        if size is not None and size.upper() not in str(item["size"]).upper():
            continue

        # score: how many keywords show up in the searchable text of this listing
        haystack = " ".join([
            item["title"],
            item["description"],
            item["category"],
            " ".join(item["style_tags"]),
            " ".join(item["colors"]),
        ]).lower()

        score = sum(1 for kw in keywords if kw in haystack)

        # if the user gave keywords, drop anything that matched none of them.
        # if they gave NO real keywords (just "size M under 30"), keep the
        # filtered results so the search still returns something.
        if keywords and score == 0:
            continue

        scored.append((score, item))

    # best score first; ties keep dataset order (stable sort)
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


# -- Tool 2: suggest_outfit ----------------------------------------------------

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1-2 complete outfits.

    Returns a non-empty string. If the wardrobe is empty, gives general styling
    advice instead of naming pieces. Never raises.
    """
    item_line = f"{new_item['title']} ({new_item['category']}, colors: {', '.join(new_item['colors'])}, tags: {', '.join(new_item['style_tags'])})"

    items = wardrobe.get("items", [])

    if not items:
        # brand new user, no closet yet -> general advice
        prompt = (
            "You are a friendly personal stylist. The user just found this secondhand piece:\n"
            f"{item_line}\n\n"
            "They haven't told you what's in their closet yet. In 2-3 sentences, suggest the "
            "kinds of pieces that would pair well with it and the overall vibe it gives. "
            "Keep it casual and specific, not a product description."
        )
    else:
        closet = "\n".join(
            f"- {it['name']} ({it['category']}, {', '.join(it['colors'])})"
            for it in items
        )
        prompt = (
            "You are a friendly personal stylist. The user just found this secondhand piece:\n"
            f"{item_line}\n\n"
            "Here is what's already in their closet:\n"
            f"{closet}\n\n"
            "Suggest 1-2 complete outfits built around the new piece, naming the specific "
            "wardrobe items they already own. Mention one small styling tip (how to wear/tuck/layer it). "
            "Keep it to 3-4 sentences, casual and specific."
        )

    try:
        return _chat(prompt, temperature=0.7, max_tokens=300)
    except Exception as e:
        # don't crash the agent just because the LLM hiccupped
        return (
            f"Couldn't reach the styling model ({e}). Based on the tags, the "
            f"{new_item['title']} would work with neutral basics and your everyday shoes."
        )


# -- Tool 3: create_fit_card ---------------------------------------------------

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Returns a 2-4 sentence caption. If `outfit` is empty/whitespace, returns a
    descriptive error string instead of calling the LLM. Never raises.
    """
    # guard: can't caption an outfit that doesn't exist
    if not outfit or not outfit.strip():
        return (
            "Can't write a fit card without an outfit — looks like the styling step "
            "didn't return anything. Try running suggest_outfit again first."
        )

    price = new_item.get("price")
    prompt = (
        "Write a short, casual Instagram/TikTok caption for an outfit-of-the-day post. "
        "It should sound like a real person who just thrifted something, NOT a product listing.\n\n"
        f"The thrifted item: {new_item['title']}, ${price}, from {new_item['platform']}.\n"
        f"The outfit: {outfit}\n\n"
        "Rules: 2-4 sentences. Mention the item name, the price, and the platform once each, "
        "naturally. Capture the vibe of the outfit in specific terms. An emoji or two is fine. "
        "Don't use hashtags spam. Just give me the caption text, nothing else."
    )

    try:
        # higher temp so the same item gives a different caption each time
        return _chat(prompt, temperature=0.9, max_tokens=200)
    except Exception as e:
        # simple hand-built fallback so the pipeline still produces *a* caption
        return (
            f"thrifted this {new_item['title'].lower()} off {new_item['platform']} for "
            f"${price} and i'm obsessed 🤍 (caption model was down: {e})"
        )
