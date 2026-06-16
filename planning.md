# FitFindr — planning.md

> I wrote this out before I built anything, so I'd actually know what I was making.
> Then I basically copy-pasted these sections into Claude when it was time to code.
> I came back and updated the search part after I added the retry thing.

---

## Tools

I've got the 3 required tools, plus one tiny helper (`parse_query`) that I pulled out
on its own so the main loop didn't get cluttered with regex. The three graded ones
come first.

### Tool 1: search_listings

**What it does:**
Looks through the 40 fake listings and gives back the ones that fit what the user typed.
It throws out anything too expensive or the wrong size first, then ranks whatever's left
by how many of the user's words actually show up in the listing.

**Input parameters:**
- `description` (str): the words describing the item, like `"vintage graphic tee"`. This is what I score the matches against.
- `size` (str, optional): the size to keep, like `"M"`. I match it loosely, so `"M"` still finds a listing tagged `"S/M"`. If it's `None`, I just skip the size check.
- `max_price` (float, optional): the most they want to spend. It's inclusive (so `30` keeps a $30 item). `None` means no price limit.

**What it returns:**
A `list[dict]`, best match first. Every dict is one whole listing and has these keys:
`id`, `title`, `description`, `category`, `style_tags` (a list), `size`, `condition`,
`price` (a float), `colors` (a list), `brand`, and `platform`. I don't bother saving the
match score onto the dict, since the order already tells you which one is best. If nothing
matches, I get back an empty list `[]`.

**What happens if it fails or returns nothing:**
It never crashes. Worst case, it just hands back `[]`. The agent looks at that empty list
and decides what to do next. It does NOT blindly shove `[]` into the next tool. The full
story is in the planning loop below.

---

### Tool 2: suggest_outfit

**What it does:**
Takes the item the user is eyeing plus their closet, and asks the model to actually put
together an outfit or two using stuff they already own.

**Input parameters:**
- `new_item` (dict): one listing dict, straight out of `search_listings` (the thrifted piece).
- `wardrobe` (dict): looks like `{"items": [...]}`, where each item has `name`, `category`, `colors`, `style_tags`, and `notes`. Can be empty.

**What it returns:**
A `str` with a few sentences of styling advice that names real pieces from their closet
(something like "wear it with your baggy straight-leg jeans and the chunky white sneakers").

**What happens if it fails or returns nothing:**
- If the closet is empty (`wardrobe["items"] == []`), I notice that and switch to a different prompt that gives general advice instead of naming pieces, so a brand new user still gets something useful.
- If the model call breaks (no internet, API down, whatever), I catch it and return a plain backup line about the item. It never throws.

---

### Tool 3: create_fit_card

**What it does:**
Turns the outfit into a short little caption, the kind you'd actually post under an OOTD pic.

**Input parameters:**
- `outfit` (str): the advice string that came out of `suggest_outfit`.
- `new_item` (dict): the listing dict again, because I need the name, price, and platform off it.

**What it returns:**
A `str`, about 2 to 4 sentences, casual, that mentions the item name, price, and platform
once each. I run this one at a higher temperature (0.9) on purpose, so the same item gives
a fresh caption each time instead of the exact same line.

**What happens if it fails or returns nothing:**
- If `outfit` is empty or just whitespace, I stop right there and return an error sentence ("Can't write a fit card without an outfit..."). I don't even bother calling the model.
- If the model call breaks, I catch it and build a simple caption by hand, so the user still gets one. Never throws.

---

### Additional Tools (helper)

**`parse_query(query) -> dict`** is not one of the graded three. I just made it so the
regex wasn't sitting in the middle of my loop, gunking it up. It returns
`{"description", "size", "max_price"}`. Price comes from stuff like `under $30`, `$30`,
or `30 dollars`. Size comes from `size M`, a lone `XS/S/M/L/XL` token, or `size 8` for
shoes. Whatever text is left over after I strip the price and size becomes the description.

---

## Planning Loop

This is how the agent figures out what to do next. It's basically a straight line with two
spots where it can bail out early, so the behavior genuinely changes based on what the
search hands back.

```
1. parse_query(query) -> description, size, max_price   (save in session["parsed"])
2. results = search_listings(description, size, max_price)
   - if results is EMPTY:
       try again without the size filter. leave a note saying I did that.
   - if STILL empty:
       try again without size AND without price. leave a note.
   - if STILL empty after all that:
       set session["error"] to a helpful message and RETURN now. (skip tools 2 and 3)
   - if there ARE results: keep going. save them in session["search_results"].
3. selected_item = results[0]   (the top-ranked one)   save in session["selected_item"]
4. outfit = suggest_outfit(selected_item, wardrobe)   save in session["outfit_suggestion"]
5. fit_card = create_fit_card(outfit, selected_item)   save in session["fit_card"]
6. return session
```

The whole point: if the search comes back with nothing, the agent stops after step 2 and
`fit_card` stays `None`. It only runs all three tools when there's actually something to
style. The retry part in step 2 was my stretch feature. It loosens the filters one at a
time before giving up, and it tells the user what it changed.

How does it know it's done? One of two things happened. Either `session["error"]` got set
(it bailed early), or `session["fit_card"]` got filled in (it worked all the way through).
The UI checks `error` first.

---

## State Management

Everything for one run lives inside a single `session` dict that `_new_session()` builds at
the start. That dict is the one source of truth for the whole run. Each tool drops its
result into it, and the next tool reads from the dict instead of asking the user again.

Here's what's in it:
- `query`: the original text they typed
- `parsed`: `{description, size, max_price}` from parse_query
- `search_results`: the list from tool 1
- `selected_item`: `search_results[0]`, the dict I hand to tools 2 AND 3
- `wardrobe`: passed straight into tool 2
- `outfit_suggestion`: the string from tool 2, then fed into tool 3
- `fit_card`: the string from tool 3
- `error`: only gets set if it bailed early
- `notes`: my own add-on, a list of little messages like "dropped the size filter" so the UI can explain what the retry did

How it moves between tools: `selected_item` is the exact same dict object that came out of
`search_listings`. I don't look it up again or retype it anywhere. `suggest_outfit` reads
it and writes a string into `outfit_suggestion`, then `create_fit_card` reads BOTH
`outfit_suggestion` and `selected_item`. Nobody gets re-prompted mid-run, and there are no
hardcoded values stuffed in between the steps.

---

## Error Handling

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | Nothing matches the query | Retry with looser filters (drop size, then drop price). If it's still empty, set `session["error"]`: "Couldn't find anything matching that, even after loosening the filters. Try fewer details, a higher price, or different keywords." Then stop, before tools 2 and 3 ever run. |
| suggest_outfit | The wardrobe is empty | Catch the empty `items` list and switch to a general-advice prompt instead of naming specific pieces, so new users still get help. If the model call fails, return a short backup line about the item. Never throws. |
| create_fit_card | The outfit input is missing or blank | Check the empty/whitespace `outfit` up front and return a clear error sentence right away. If the model call fails, return a simple template caption instead. Never throws. |

---

## Architecture

```
        User query  +  wardrobe choice
                  |
                  v
        +-----------------------------+
        |       Planning Loop         | <----------- session dict (the state)
        |       (run_agent)           |              query, parsed, search_results,
        +-----------------------------+              selected_item, wardrobe,
                  |                                  outfit_suggestion, fit_card,
        parse_query(query)                           error, notes
                  |  description, size, max_price
                  v
        search_listings(description, size, max_price)
                  |
                  |  results == []  --> retry: drop size --> still [] --> retry: drop price
                  |                                                            |
                  |                                                       still [] ?
                  |                                                            |
                  +--> [ERROR] set session["error"] ---------------------------+
                  |                                                            |
                  |                                          return session (STOP, skip tools 2 & 3)
                  |
                  |  results == [item, ...]
                  v
        session["selected_item"] = results[0]
                  |  the top-ranked listing dict
                  v
        suggest_outfit(selected_item, wardrobe)
                  |  empty wardrobe?  --> general advice instead
                  v
        session["outfit_suggestion"] = "..."
                  |  the styling string
                  v
        create_fit_card(outfit_suggestion, selected_item)
                  |  empty outfit?  --> error string instead
                  v
        session["fit_card"] = "..."
                  |
                  v
        return session   <-- the error path ends up here too
```

---

## AI Tool Plan

I used **Claude** for all the coding help, since that's what I have set up. (Quick note:
at runtime the tools themselves call Groq's `llama-3.3-70b-versatile` model for the
styling and caption text. But for writing the actual Python, I leaned on Claude.)

**Milestone 3 (the individual tools):**
- For each tool, I copy that tool's whole block from the Tools section above (what it does, the inputs, the return value, the failure mode) and paste it into Claude one tool at a time, and ask it to write just that one function in `tools.py`.
- For `search_listings`, I specifically tell it to use `load_listings()` from the data loader and NOT re-read the file itself. Before I trust the result, I check two things: does it actually filter by all three params, and does it return `[]` (instead of crashing) when nothing matches. Then I run my 3 pytest cases on it.
- For `suggest_outfit` and `create_fit_card`, I check that the empty-wardrobe branch and the empty-outfit branch are both actually there before I run anything, then I call them with a hardcoded `search_listings` result so I'm not waiting on the search every time.

**Milestone 4 (the planning loop and state):**
- I give Claude the Architecture diagram plus the Planning Loop and State Management sections, and ask it to write `run_agent`. Before I run it, I check: does it branch on the search result (instead of just calling all three tools every single time), does it write into the `session` dict at each step, and does it return early on the empty/error path. Then I run `python agent.py` so I can watch both the happy path and the no-results path with my own eyes.

---

## A Complete Interaction (Step by Step)

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy
jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1, parse and search.**
`parse_query` pulls out `description="vintage graphic tee"`, `size=None`, and
`max_price=30.0`. The agent calls `search_listings("vintage graphic tee", None, 30.0)`. It
comes back with a few tees under $30, ranked by how many keywords matched. The top one ends
up being the Y2K baby tee at $18 from depop. That whole list gets saved in
`session["search_results"]`.

**Step 2, pick one and style it.**
The agent sets `session["selected_item"] = search_results[0]` (the $18 tee) and calls
`suggest_outfit(selected_item, example_wardrobe)`. The model sees the tee plus the user's
real closet (baggy straight-leg jeans, chunky white sneakers, a denim jacket, and so on)
and returns something like "tuck it into your baggy straight-leg jeans, throw the denim
jacket over it, and finish with the chunky white sneakers." That gets saved in
`session["outfit_suggestion"]`.

**Step 3, make the fit card.**
The agent calls `create_fit_card(outfit_suggestion, selected_item)`. It returns a caption
like "thrifted this tee off depop for $18 and it was MADE for my baggy jeans 🤍 full fit
loading." That gets saved in `session["fit_card"]`.

**Final output to user:**
Three panels. The listing it found (title, price, platform, condition, size), the outfit
idea, and the shareable fit card. If the search had come back empty instead, the first
panel would just show the error message and the other two would stay blank.
