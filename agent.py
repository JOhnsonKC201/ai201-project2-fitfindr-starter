"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

from tools import search_listings, suggest_outfit, create_fit_card, parse_query


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run. It stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    Added a "notes" list so I can tell the user when the retry loosened a filter.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
        "notes": [],                 # messages about what the agent adjusted (retry)
    }


# ── search step (with retry fallback) ─────────────────────────────────────────

def _search_with_retry(session: dict) -> list[dict]:
    """
    Try the search. If it comes back empty, loosen the filters and try again
    before giving up. First drop the size, then drop the price too. Each time we
    loosen something we leave a note so the UI can tell the user what changed.

    This is the "retry logic with fallback" stretch feature.
    """
    p = session["parsed"]
    desc, size, price = p["description"], p["size"], p["max_price"]

    # attempt 1: exactly what the user asked for
    results = search_listings(desc, size, price)
    if results:
        return results

    # attempt 2: drop the size filter (most common reason for zero hits)
    if size is not None:
        results = search_listings(desc, None, price)
        if results:
            session["notes"].append(
                f"Nothing in size {size}, so I dropped the size filter to show close matches."
            )
            return results

    # attempt 3: drop the price ceiling too
    if price is not None:
        results = search_listings(desc, None, None)
        if results:
            session["notes"].append(
                f"Nothing under ${price:g} in that, so I widened the price range."
            )
            return results

    # genuinely nothing
    return []


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for one user
    interaction and returns the finished session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M").
        wardrobe: User's wardrobe dict. Use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py.

    Returns:
        The session dict after the interaction finishes. Check session["error"]
        first. If it isn't None, the run ended early and the other output fields
        (outfit_suggestion, fit_card) will still be None.

    The flow follows the planning loop from planning.md:
        1. _new_session() to set up the state.
        2. parse_query() to pull description / size / max_price into session["parsed"].
        3. _search_with_retry() to call search_listings, loosening the filters if it
           comes back empty. If it's still empty, set session["error"] and return
           early so we never hand an empty list to suggest_outfit.
        4. Pick results[0] as session["selected_item"].
        5. suggest_outfit() into session["outfit_suggestion"].
        6. create_fit_card() into session["fit_card"].
        7. Return the session.
    """
    session = _new_session(query, wardrobe)

    # Step 1 + 2: parse the query, then search (with the retry fallback).
    session["parsed"] = parse_query(query)
    results = _search_with_retry(session)
    session["search_results"] = results

    # Step 3: branch. No item -> set an error and STOP. We never hand an empty
    # list to suggest_outfit. This is what makes the agent's behavior actually
    # depend on what came back instead of running all 3 tools no matter what.
    if not results:
        session["error"] = (
            "Couldn't find anything matching that, even after loosening the filters. "
            "Try fewer details, a higher price, or different keywords "
            "(e.g. 'denim jacket' instead of 'studded cropped denim jacket size XS')."
        )
        return session

    # Step 4: pick the top-ranked item and pass it forward via the session.
    session["selected_item"] = results[0]

    # Step 5: style it against the wardrobe.
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], session["wardrobe"]
    )

    # Step 6: caption it. Reads BOTH the outfit string and the selected item.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 7: done.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
