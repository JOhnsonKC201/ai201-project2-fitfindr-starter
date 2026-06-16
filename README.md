# FitFindr 🛍️

FitFindr is a multi-tool AI agent that helps you thrift secondhand clothes and figure out how
to actually wear them. You type what you want in plain English, and the agent searches the
listings, styles the best find against your closet, and writes you a little fit card you could
post. If a step has nothing to work with, it stops cleanly instead of crashing.

Built for AI201 Project 2.

---

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); use .venv/bin/activate on Mac/Linux
pip install -r requirements.txt
```

Grab a free Groq key from [console.groq.com](https://console.groq.com) and drop it into a
`.env` file in the project root. It's gitignored, so don't commit it:

```
GROQ_API_KEY=your_key_here
```

**Run the app:**
```bash
python app.py
```
Then open the localhost URL it prints (usually http://localhost:7860).

**Run from the terminal / run the tests:**
```bash
python agent.py          # happy path + no-results path
pytest tests/ -q         # 12 tests, runs offline (the LLM calls are mocked)
```

---

## Files

```
tools.py        # the 3 tools + the parse_query helper
agent.py        # run_agent(), the planning loop + session state + retry fallback
app.py          # Gradio UI (handle_query maps the session into 3 panels)
tests/          # pytest tests, at least one per failure mode
data/           # 40 mock listings + the wardrobe schema
utils/          # data_loader.py (provided)
planning.md     # the spec I wrote before I coded
```

---

## Tool Inventory

The inputs and returns below match the real signatures in `tools.py`.

### `search_listings(description, size, max_price) -> list[dict]`
- **Inputs:** `description (str)` keywords; `size (str | None)` size filter, matched as a case-insensitive substring (so `"M"` finds `"S/M"`); `max_price (float | None)` inclusive price ceiling.
- **Returns:** a list of listing dicts (`id, title, description, category, style_tags, size, condition, price, colors, brand, platform`), best match first. Empty list `[]` if nothing matches. Never raises.
- **Purpose:** find and rank the matching secondhand listings.

### `suggest_outfit(new_item, wardrobe) -> str`
- **Inputs:** `new_item (dict)` one listing; `wardrobe (dict)` `{"items": [...]}`, can be empty.
- **Returns:** a non-empty string with 1 or 2 outfit ideas that name pieces the user already owns.
- **Purpose:** style the found item against the user's real closet (uses the LLM).

### `create_fit_card(outfit, new_item) -> str`
- **Inputs:** `outfit (str)` the suggestion from `suggest_outfit`; `new_item (dict)` the listing.
- **Returns:** a 2 to 4 sentence casual caption (mentions the name, price, and platform once each). It comes out different each run (temp 0.9). Returns an error string if `outfit` is empty. Never raises.
- **Purpose:** write the shareable OOTD caption (uses the LLM).

### `parse_query(query) -> dict` (helper)
- **Inputs:** `query (str)` the raw user text.
- **Returns:** `{"description": str, "size": str | None, "max_price": float | None}`, pulled out with regex.
- **Purpose:** keep the regex out of the planning loop so the loop stays readable.

---

## How the Planning Loop Works

`run_agent(query, wardrobe)` in `agent.py` is a straight pipeline with two branch points, so
what the agent does really depends on what comes back. It does not just fire all three tools
no matter what.

1. `parse_query(query)` gives back description / size / max_price.
2. Search (with retry): call `search_listings`. Branch: if it returns `[]`, try again with the size filter dropped; if it's still `[]`, try again with the price ceiling dropped too. Every time it loosens a filter it saves a note (like "Nothing in size XXS, so I dropped the size filter").
3. Branch: if the search is still empty after all that, set `session["error"]` and return early. `suggest_outfit` and `create_fit_card` never run, and `fit_card` stays `None`.
4. Otherwise grab `results[0]` (the top-ranked one) as `selected_item`.
5. `suggest_outfit(selected_item, wardrobe)` gives back `outfit_suggestion`.
6. `create_fit_card(outfit_suggestion, selected_item)` gives back `fit_card`.
7. Return the session.

It's "done" when either `error` got set (early exit) or `fit_card` got filled in. The UI checks
`error` first. The retry in step 2 is the retry-with-fallback stretch feature.

---

## State Management

There's one `session` dict per run, built by `_new_session()`, and it's the single source of
truth. Each tool writes its output into the session, and the next tool reads from the session
instead of from the user, so nothing gets re-entered halfway through.

| Field | Set by | Read by |
|-------|--------|---------|
| `query` | entry | (kept for reference) |
| `parsed` | `parse_query` | search step |
| `search_results` | `search_listings` | item selection |
| `selected_item` | `results[0]` | `suggest_outfit` and `create_fit_card` |
| `wardrobe` | caller | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | UI |
| `error` | loop (early exit) | UI |
| `notes` | retry step | UI |

`selected_item` is the exact same dict object that `search_listings` returned. It doesn't get
re-looked-up or retyped between steps. You can check it yourself:
`session["selected_item"] is session["search_results"][0]` comes back `True`.

---

## Error Handling

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| `search_listings` | No results match | Returns `[]` (never raises). The loop retries with looser filters (drop size, then drop price). If it's still empty, it sets a helpful `error` message and stops before the other tools. |
| `suggest_outfit` | Empty wardrobe | Catches `items == []` and switches to a general-styling prompt so a brand new user still gets advice. LLM or network errors are caught and return a short fallback string. |
| `create_fit_card` | Missing or empty outfit | Guards the empty/whitespace string up front and returns a clear message without calling the LLM. LLM errors fall back to a simple hand-built caption. |

Concrete example from my own testing: calling `create_fit_card("", listing)` returns "Can't
write a fit card without an outfit. Looks like the styling step didn't return anything, try
running suggest_outfit again first." That's a string, not an exception. And the no-results query
"designer ballgown size XXS under $5" shows the search error in panel 1 with the outfit and
fit-card panels left blank, because the agent never reached those tools.

---

## Interaction Walkthrough

User query: "vintage graphic tee under $30" (with the example wardrobe).

**Step 1, `search_listings`**
- Input: `parse_query` gives `description="vintage graphic tee", size=None, max_price=30.0`.
- Why: I need an actual item before anything else can run.
- Output: a bunch of ranked tees under $30. Top hit is the Y2K Baby Tee (Butterfly Print), $18, depop. Saved in `session["selected_item"]`.

**Step 2, `suggest_outfit`**
- Input: the Y2K tee dict plus the example wardrobe.
- Why: there's an item now, and a non-empty closet to style it against.
- Output: "pair it with your baggy straight-leg jeans and chunky white sneakers, tuck the front to define the waist." Saved in `session["outfit_suggestion"]`.

**Step 3, `create_fit_card`**
- Input: that outfit string plus the same tee dict.
- Why: turn the finished look into something you could actually post.
- Output: "Just scored this adorable Y2K Baby Tee for $18 on depop and I'm obsessed 🦋..." Saved in `session["fit_card"]`.

Final output: three panels, the listing, the outfit idea, and the fit card. (If the search had
come back empty, panel 1 would show the error and the other two would stay blank.)

---

## Spec Reflection

One way `planning.md` actually helped: writing the error-handling table and the planning-loop
branches first meant I'd already decided "empty search, retry, otherwise error and stop" before
I ever touched `agent.py`. So when I wrote `run_agent`, I was basically just transcribing the
diagram, and I never hit that "wait, what happens if the search is empty here?" panic in the
middle of coding. That call was already made and written down.

One thing I changed from the spec, and why: my plan only had the three required tools, but
while I was building it I pulled the query parsing out into its own `parse_query` helper (and
added a `notes` field to the session for the retry messages). The spec assumed the loop would
parse inline, but doing the regex inside `run_agent` made it hard to read and hard to test, so
splitting it out was cleaner. I went back and updated `planning.md` to list `parse_query` as a
helper instead of pretending it wasn't there.

---

## AI Usage

I used Claude the whole way through, the way the course's AI Tool Plan describes: I directed it
with my spec, then checked what it gave me against that spec.

1. `search_listings` (Milestone 3). I gave Claude the Tool 1 block from `planning.md` (inputs, return value, failure mode) and asked it to write the function using `load_listings()`. Its first try did a plain substring match on the title only, which missed listings whose keywords lived in the `style_tags`. I changed it to score across the title, description, tags, and colors, and added a stopword list so filler words like "looking" and "for" didn't pad the score. Checked it with the three pytest cases plus a manual "vintage graphic tee under $30" run.

2. The planning loop + retry (Milestone 4). I gave Claude the Architecture diagram and the Planning Loop + State Management sections and asked it to write `run_agent`. Its first version called all three tools and only checked for emptiness at the very end, which is exactly the "fixed sequence no matter the context" thing the rubric warns against. I rewrote it to return early the second the search is empty, then added the retry-with-fallback on top (`_search_with_retry`) so it loosens filters before giving up. Checked it by running `python agent.py` and confirming the no-results path leaves `fit_card = None`.

---

## Stretch Feature: Retry with Fallback

When `search_listings` comes back empty, the agent doesn't just give up. It retries with the
size filter dropped, then with the price ceiling dropped, and it tells the user what it changed
with a note above the listing. It's all in `_search_with_retry` in `agent.py`. For example,
"vintage graphic tee size XXS" finds no XXS tee, so it drops the size and shows close matches
with the note "Nothing in size XXS, so I dropped the size filter to show close matches."
