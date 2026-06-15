# FitFindr 🛍️

A multi-tool AI agent that helps you thrift secondhand pieces and figure out how to
wear them. You describe what you're looking for in plain English; the agent searches
the listings, styles the best find against your wardrobe, and writes you a shareable
fit card — and it bails out gracefully when a step has nothing to work with.

Built for AI201 Project 2.

---

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); use .venv/bin/activate on Mac/Linux
pip install -r requirements.txt
```

Add your free Groq key (from [console.groq.com](https://console.groq.com)) to a `.env`
file in the project root — it's gitignored, don't commit it:

```
GROQ_API_KEY=your_key_here
```

**Run the app:**
```bash
python app.py
```
Then open the localhost URL it prints (usually http://localhost:7860).

**Run the tools from the terminal / run the tests:**
```bash
python agent.py          # happy path + no-results path
pytest tests/ -q         # 12 tests, runs offline (LLM calls are mocked)
```

---

## Files

```
tools.py        # the 3 tools + parse_query helper
agent.py        # run_agent() — the planning loop + session state + retry fallback
app.py          # Gradio UI (handle_query maps the session to 3 panels)
tests/          # pytest tests, one+ per failure mode
data/           # 40 mock listings + wardrobe schema
utils/          # data_loader.py (provided)
planning.md     # the spec I wrote before coding
```

---

## Tool Inventory

Inputs/returns below match the actual signatures in `tools.py`.

### `search_listings(description, size, max_price) -> list[dict]`
- **Inputs:** `description (str)` keywords; `size (str | None)` size filter, case-insensitive substring (`"M"` matches `"S/M"`); `max_price (float | None)` inclusive price ceiling.
- **Returns:** list of listing dicts (`id, title, description, category, style_tags, size, condition, price, colors, brand, platform`), best match first. `[]` if nothing matches — never raises.
- **Purpose:** find and rank matching secondhand listings.

### `suggest_outfit(new_item, wardrobe) -> str`
- **Inputs:** `new_item (dict)` a listing; `wardrobe (dict)` `{"items": [...]}`, may be empty.
- **Returns:** a non-empty string with 1–2 outfit ideas naming pieces the user owns.
- **Purpose:** style the found item against the user's actual closet (LLM-backed).

### `create_fit_card(outfit, new_item) -> str`
- **Inputs:** `outfit (str)` the suggestion from `suggest_outfit`; `new_item (dict)` the listing.
- **Returns:** a 2–4 sentence casual caption (mentions name, price, platform once each). Different each run (temp 0.9). Returns an error string if `outfit` is empty — never raises.
- **Purpose:** write the shareable OOTD caption (LLM-backed).

### `parse_query(query) -> dict` (helper)
- **Inputs:** `query (str)` the raw user text.
- **Returns:** `{"description": str, "size": str | None, "max_price": float | None}` via regex.
- **Purpose:** keep the regex out of the planning loop so it reads cleanly.

---

## How the Planning Loop Works

`run_agent(query, wardrobe)` in `agent.py` runs a linear pipeline with **two branch
points**, so the agent's behavior actually depends on what comes back — it does *not*
fire all three tools no matter what.

1. `parse_query(query)` → description / size / max_price.
2. **Search (with retry):** call `search_listings`. **Branch:** if it returns `[]`, retry
   with the size filter dropped; if still `[]`, retry with the price ceiling dropped too.
   Each time it loosens a filter it records a note (e.g. *"Nothing in size XXS, so I
   dropped the size filter"*).
3. **Branch:** if the search is *still* empty, set `session["error"]` and **return early** —
   `suggest_outfit` and `create_fit_card` are never called, and `fit_card` stays `None`.
4. Otherwise pick `results[0]` (top ranked) as `selected_item`.
5. `suggest_outfit(selected_item, wardrobe)` → `outfit_suggestion`.
6. `create_fit_card(outfit_suggestion, selected_item)` → `fit_card`.
7. Return the session.

The whole thing is "done" when either `error` is set (early exit) or `fit_card` is filled.
The UI checks `error` first. The retry in step 2 is the **retry-with-fallback stretch feature**.

---

## State Management

There's one `session` dict per run, created by `_new_session()`, and it's the single
source of truth. Each tool **writes its output into the session**, and the next tool
**reads from the session** instead of from the user — so nothing gets re-entered mid-run.

| Field | Set by | Read by |
|-------|--------|---------|
| `query` | entry | — |
| `parsed` | `parse_query` | search step |
| `search_results` | `search_listings` | item selection |
| `selected_item` | `results[0]` | `suggest_outfit` **and** `create_fit_card` |
| `wardrobe` | caller | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | UI |
| `error` | loop (early exit) | UI |
| `notes` | retry step | UI |

`selected_item` is the exact same dict object returned by `search_listings` — it's not
re-looked-up or retyped between steps. You can verify:
`session["selected_item"] is session["search_results"][0]` is `True`.

---

## Error Handling

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| `search_listings` | No results match | Returns `[]` (never raises). The loop retries with looser filters (drop size → drop price). If still empty, it sets a helpful `error` message and stops before the other tools. |
| `suggest_outfit` | Empty wardrobe | Detects `items == []` and switches to a general-styling prompt so a brand-new user still gets advice. LLM/network errors are caught and return a short fallback string. |
| `create_fit_card` | Missing/empty outfit | Guards the empty/whitespace string up front and returns a descriptive message *without* calling the LLM. LLM errors fall back to a simple hand-built caption. |

**Concrete example (from my testing):** running
`create_fit_card("", listing)` returns
*"Can't write a fit card without an outfit — looks like the styling step didn't return
anything. Try running suggest_outfit again first."* — a string, not an exception. And the
no-results query `"designer ballgown size XXS under $5"` returns the search error in panel 1
with the outfit/fit-card panels left empty, because the agent never reached those tools.

---

## Interaction Walkthrough

**User query:** *"vintage graphic tee under $30"* (with the example wardrobe).

**Step 1 — `search_listings`**
- Input: `parse_query` → `description="vintage graphic tee", size=None, max_price=30.0`.
- Why: need an actual item before anything else can run.
- Output: ~20 ranked tees under $30; top hit = **Y2K Baby Tee — Butterfly Print, $18, depop**.
  Stored in `session["selected_item"]`.

**Step 2 — `suggest_outfit`**
- Input: the Y2K tee dict + the example wardrobe.
- Why: there's an item now, and a non-empty wardrobe to style it against.
- Output: *"pair it with your baggy straight-leg jeans and chunky white sneakers… tuck the
  front to define the waist."* Stored in `session["outfit_suggestion"]`.

**Step 3 — `create_fit_card`**
- Input: that outfit string + the same tee dict.
- Why: turn the finished look into something shareable.
- Output: *"Just scored this adorable Y2K Baby Tee for $18 on depop and I'm obsessed 🦋…"*
  Stored in `session["fit_card"]`.

**Final output:** three panels — the listing, the outfit idea, and the fit card. (If the
search had returned nothing, panel 1 would show the error and the other two stay empty.)

---

## Spec Reflection

**One way `planning.md` helped:** Writing the error-handling table and the planning-loop
branches *first* meant I'd already decided "empty search → retry → else error and stop"
before I touched `agent.py`. So when I wrote `run_agent` it was basically transcribing the
diagram, and I never had the "wait, what happens if search is empty here" moment mid-code —
that decision was already made and written down.

**One divergence, and why:** My spec only had the three required tools, but while
implementing I pulled the query parsing out into a separate `parse_query` helper (and added
a `notes` field to the session for the retry messages). The spec implied the loop would
parse inline, but doing the regex inside `run_agent` made it hard to read and hard to test,
so splitting it out was cleaner. I updated `planning.md` to list `parse_query` as a helper
rather than pretend it wasn't there.

---

## AI Usage

I used **Claude** throughout, the way the course's AI Tool Plan describes — directing it
with my spec, then checking the output against it.

1. **`search_listings` (Milestone 3).** I gave Claude the Tool 1 block from `planning.md`
   (inputs, return value, failure mode) and asked it to implement the function using
   `load_listings()`. The first cut did a plain substring match on the title only, which
   missed listings whose keywords were in the `style_tags`. I **changed it** to score across
   title + description + tags + colors, and added a stopword list so filler words like
   "looking"/"for" didn't inflate the score. Verified with the three pytest cases plus a
   manual "vintage graphic tee under $30" run.

2. **The planning loop + retry (Milestone 4).** I gave Claude the Architecture diagram and
   the Planning Loop + State Management sections and asked it to implement `run_agent`. Its
   first version called all three tools and only checked for emptiness at the end — which is
   exactly the "fixed sequence regardless of context" the rubric warns against. I **rewrote**
   it to return early the moment the search is empty, and then layered the retry-with-fallback
   on top (`_search_with_retry`) so it loosens filters before giving up. Verified by running
   `python agent.py` and confirming the no-results path leaves `fit_card = None`.

---

## Stretch Feature: Retry with Fallback

When `search_listings` returns nothing, the agent doesn't immediately give up — it retries
with the size filter dropped, then with the price ceiling dropped, and tells the user what
it adjusted via a note shown above the listing. Implemented in `_search_with_retry` in
`agent.py`. Example: *"vintage graphic tee size XXS"* finds no XXS tee, so it drops the size
and surfaces close matches with the note *"Nothing in size XXS, so I dropped the size filter
to show close matches."*
