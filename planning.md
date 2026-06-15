# FitFindr — planning.md

> Wrote this out before coding so I'd actually know what each tool needs.
> Ended up using these sections as the prompts I fed Claude for each milestone.
> Updated the "Stretch" note before I added the retry fallback.

---

## Tools

3 required tools + 1 small helper I split out (`parse_query`) so the agent isn't
doing regex inline. Listing the required three first.

### Tool 1: search_listings

**What it does:**
Searches the 40 mock listings and returns the ones that match what the user asked
for. Filters by price + size first, then ranks whatever's left by how many of the
user's keywords show up in the listing.

**Input parameters:**
- `description` (str): keywords describing the item, e.g. `"vintage graphic tee"`. This is what I score relevance against.
- `size` (str | None): size to filter by, e.g. `"M"`. Case-insensitive substring match so `"M"` matches `"S/M"`. `None` = don't filter by size.
- `max_price` (float | None): price ceiling, inclusive. `None` = don't filter by price.

**What it returns:**
A `list[dict]`, best match first. Each dict is a full listing with these keys:
`id, title, description, category, style_tags (list), size, condition, price (float), colors (list), brand, platform`.
I do NOT attach the score to the dict — the order already encodes it. Empty list if nothing matches.

**What happens if it fails or returns nothing:**
Returns `[]` (never raises). The agent sees the empty list and decides what to do —
it does not blindly hand `[]` to `suggest_outfit`. See the planning loop + retry note below.

---

### Tool 2: suggest_outfit

**What it does:**
Takes the item the user is thinking about buying plus their current wardrobe, and
asks the LLM to put together 1–2 actual outfits using named pieces they already own.

**Input parameters:**
- `new_item` (dict): one listing dict from `search_listings` (the thrifted piece).
- `wardrobe` (dict): `{"items": [...]}` where each item has `name, category, colors, style_tags, notes`. Can be empty.

**What it returns:**
A non-empty `str` — a couple sentences of styling advice that name real wardrobe
pieces (e.g. "pair it with your baggy straight-leg jeans and chunky white sneakers").

**What happens if it fails or returns nothing:**
- Empty wardrobe → I detect `wardrobe["items"] == []` and switch to a "general styling"
  prompt instead (what to pair it with in general, what vibe it gives) so a brand-new
  user still gets something useful.
- LLM/network error → caught, returns a plain fallback string describing the item so
  the agent can still move on to the fit card. Never raises.

---

### Tool 3: create_fit_card

**What it does:**
Turns the outfit into a short caption you'd actually put under an OOTD post.

**Input parameters:**
- `outfit` (str): the suggestion string from `suggest_outfit`.
- `new_item` (dict): the listing dict (for the name, price, platform).

**What it returns:**
A `str`, ~2–4 sentences, casual, mentions the item name + price + platform once each.
Higher temperature (0.9) so the same item phrased differently gives different captions.

**What happens if it fails or returns nothing:**
- `outfit` empty / whitespace-only → returns a descriptive error string
  (`"Can't write a fit card without an outfit..."`), does NOT call the LLM, does NOT raise.
- LLM error → caught, returns a simple hand-built caption as a fallback.

---

### Additional Tools (helper)

**`parse_query(query) -> dict`** — not one of the 3 graded tools, just pulled the regex
out of the agent so the loop reads cleanly. Returns `{"description", "size", "max_price"}`.
Uses regex: price from `under $30` / `$30` / `30 dollars`; size from `size M` or a
standalone `XS/S/M/L/XL/XXS/XXL` token or `size 8` for shoes.

---

## Planning Loop

**How the agent decides what to call next** — it's a linear pipeline with two early-exit
branches, so behavior genuinely changes based on what `search_listings` returns.

```
1. parse_query(query) -> description, size, max_price   (store in session["parsed"])
2. results = search_listings(description, size, max_price)
   - if results is EMPTY:
       -> RETRY: drop the size filter, search again. record what we loosened.
   - if still EMPTY:
       -> RETRY: drop size AND price, search again. record it.
   - if STILL empty:
       -> set session["error"] = helpful message, RETURN early.  (don't call tool 2 or 3)
   - else: keep going. session["search_results"] = results
3. selected_item = results[0]   (top ranked)        store in session["selected_item"]
4. outfit = suggest_outfit(selected_item, wardrobe)  store in session["outfit_suggestion"]
5. fit_card = create_fit_card(outfit, selected_item) store in session["fit_card"]
6. return session
```

The key point: if the search comes back empty the agent terminates after step 2 and the
fit card stays `None`. It only runs the full 3-tool chain when there's actually an item to
style. The retry branch (step 2) is the stretch feature — it makes one or two more attempts
with looser filters before giving up, and tells the user what it changed.

When is it "done"? Either `session["error"]` got set (early exit) or `session["fit_card"]`
got filled in (happy path). The UI checks `error` first.

---

## State Management

Everything lives in one `session` dict created by `_new_session()`. It's the single source
of truth for the run — each tool writes its output into it, and the next tool reads from it
instead of from the user.

Tracked fields:
- `query` — original text
- `parsed` — `{description, size, max_price}` from parse_query
- `search_results` — list from tool 1
- `selected_item` — `search_results[0]`, the dict passed into tools 2 AND 3
- `wardrobe` — passed straight through to tool 2
- `outfit_suggestion` — string from tool 2, passed into tool 3
- `fit_card` — string from tool 3
- `error` — set only on early exit
- `notes` — (my addition) a list of messages like "loosened the size filter" so the UI can
  tell the user what the retry did

How it's passed: `selected_item` is the exact same dict object that came out of
`search_listings` — I don't re-look-it-up or re-type it. `suggest_outfit` reads it, returns
a string into `outfit_suggestion`, and `create_fit_card` reads BOTH `outfit_suggestion` and
`selected_item`. No re-prompting the user mid-run, no hardcoded values between steps.

---

## Error Handling

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Retry with looser filters (drop size, then drop price). If still nothing, set `session["error"]`: "Couldn't find anything matching that — even after loosening the filters. Try fewer details, a higher price, or different keywords." Stop before tools 2/3. |
| suggest_outfit | Wardrobe is empty | Detect empty `items`, switch to a general-styling LLM prompt instead of naming pieces, so new users still get advice. On LLM error, return a short fallback describing the item. Never raises. |
| create_fit_card | Outfit input is missing or incomplete | Guard the empty/whitespace `outfit` string up front and return a descriptive error string. On LLM error, return a simple template caption. Never raises. |

---

## Architecture

```
                 user query (+ wardrobe choice)
                          │
                          ▼
          ┌───────────────────────────────┐
          │        Planning Loop           │◄──────── session dict
          │        (run_agent)             │          (state: query, parsed,
          └───────────────────────────────┘           search_results, selected_item,
                          │                            wardrobe, outfit_suggestion,
        parse_query()     │                            fit_card, error, notes)
                          ▼
        ┌── search_listings(description, size, max_price)
        │        │
        │        │ results == []  ── retry: drop size ── still [] ── retry: drop price
        │        │                                                        │
        │        │                                                   still [] ?
        │        │                                                        │
        │        ├──────────────────────────► [ERROR] set session.error ──┘──► return
        │        │
        │        │ results == [item, ...]
        │        ▼
        │   session.selected_item = results[0]
        │        │
        ├── suggest_outfit(selected_item, wardrobe) ── empty wardrobe? ── general advice
        │        │
        │   session.outfit_suggestion = "..."
        │        │
        └── create_fit_card(outfit_suggestion, selected_item) ── empty outfit? ── error str
                 │
            session.fit_card = "..."
                 │
                 ▼
            return session  ◄── error path also returns here
```

---

## AI Tool Plan

Using **Claude** for all of it (it's what I have set up).

**Milestone 3 — Individual tool implementations:**
- For each tool I paste that tool's block above (what it does / inputs / return / failure mode)
  into Claude one at a time and ask it to implement just that function in `tools.py`.
- For `search_listings` I tell it to use `load_listings()` from the data loader and NOT re-read
  the file. Before trusting it I check: does it filter by all 3 params? does it return `[]`
  (not crash) when nothing matches? Then I run my 3 pytest cases.
- For `suggest_outfit` / `create_fit_card` I check the empty-wardrobe and empty-outfit branches
  exist before running, then call them with a hardcoded `search_listings` result.

**Milestone 4 — Planning loop and state management:**
- I give Claude the Architecture diagram + the Planning Loop + State Management sections and ask
  it to implement `run_agent`. Before running I check: does it branch on the `search_listings`
  result (not call all 3 every time)? does it write into the `session` dict at each step? does
  it return early on the empty/error path? Then I run `python agent.py` to see both the happy
  path and the no-results path.

---

## A Complete Interaction (Step by Step)

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy
jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1 — parse + search.**
`parse_query` pulls `description="vintage graphic tee"`, `size=None`, `max_price=30.0`.
Agent calls `search_listings("vintage graphic tee", None, 30.0)`. Comes back with several
under-$30 tees ranked by keyword overlap — top one is the Y2K Baby Tee ($18, depop) /
faded band tee depending on scoring. Stored in `session["search_results"]`.

**Step 2 — pick + suggest outfit.**
Agent sets `session["selected_item"] = search_results[0]` and calls
`suggest_outfit(selected_item, example_wardrobe)`. The LLM sees the tee + the user's actual
closet (baggy straight-leg jeans, chunky white sneakers, denim jacket...) and returns
something like "tuck it into your baggy straight-leg jeans, throw the vintage denim jacket
over, finish with the chunky white sneakers." Stored in `session["outfit_suggestion"]`.

**Step 3 — fit card.**
Agent calls `create_fit_card(outfit_suggestion, selected_item)`. Returns a caption like
"thrifted this tee off depop for $18 and it was MADE for my baggy jeans 🤍 full fit loading."
Stored in `session["fit_card"]`.

**Final output to user:**
Three panels — the listing it found (title, price, platform, condition), the outfit idea,
and the shareable fit card. If the search had come back empty instead, they'd just see the
error message in the first panel and the other two stay empty.
