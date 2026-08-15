"""Web search.

Note the trade: everything else Nova does stays on the machine, but a search
query necessarily leaves it — DuckDuckGo has to receive the words you asked
about. That's why these tools sit behind `[web] enabled` and are the only ones
in the project that touch the network at request time.
"""

from __future__ import annotations

from urllib.parse import quote

from .registry import enum, integer, osascript, quote_applescript, run, string, tool

ENGINES = {
    "google": "https://www.google.com/search?q={}",
    "duckduckgo": "https://duckduckgo.com/?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
}


@tool(
    "Search the web and get back real results. Use this for anything you do "
    "not know, anything about current events, or any question about the "
    "outside world — prices, weather, news, facts, people, documentation. "
    "Summarise the results in one or two spoken sentences.",
    {
        "query": string("What to search for."),
        "limit": integer("How many results to consider, default 5."),
    },
    ["query"],
    requires="web",
)
def web_search(query: str, limit: int = 5) -> str:
    query = query.strip()
    if not query:
        return "Nothing to search for."
    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: the `ddgs` package isn't installed."

    limit = max(1, min(10, int(limit)))
    try:
        results = DDGS().text(query, max_results=limit)
    except Exception as exc:
        return f"Error: the search failed. {exc}"

    if not results:
        return f"No results for {query!r}."

    lines = []
    for item in results[:limit]:
        title = (item.get("title") or "").strip()
        body = " ".join((item.get("body") or "").split())
        if len(body) > 320:
            body = body[:320] + "…"
        lines.append(f"- {title}: {body}")
    return "\n".join(lines)


@tool(
    "Open a search in the browser and type the query in on screen, character "
    "by character, so the user can watch it happen. Use this when the user "
    "wants to SEE the search, wants results on screen, or asks for videos, "
    "images or a page to browse. For a spoken answer use web_search instead.",
    {
        "query": string("What to search for."),
        "engine": enum("Where to search.", ["google", "duckduckgo", "youtube"]),
    },
    ["query"],
    requires="web",
)
def open_web_search(query: str, engine: str = "google") -> str:
    query = query.strip()
    if not query:
        return "Nothing to search for."

    typed = _type_into_browser(query, engine)
    if typed is None:
        return f"Typed {query} into {engine} and searched."

    # Accessibility wasn't granted, or the browser wouldn't cooperate. Fall
    # back to loading the results directly — the user still gets their search.
    url = ENGINES.get(engine, ENGINES["google"]).format(quote(query, safe=""))
    result = run(["open", url])
    if result.startswith("Error"):
        return result
    return f"Opened {engine} results for {query}. ({typed})"


def _type_into_browser(query: str, engine: str) -> str | None:
    """Drive the keyboard to type the query. None means it worked.

    Returns a reason string on failure, because live typing needs
    Accessibility permission that the user may not have granted.
    """
    home = {
        "google": "https://www.google.com",
        "duckduckgo": "https://duckduckgo.com",
        "youtube": "https://www.youtube.com",
    }.get(engine, "https://www.google.com")

    script = f"""
    tell application "Safari"
      activate
      if (count of windows) is 0 then
        make new document with properties {{URL:"{home}"}}
      else
        set URL of current tab of front window to "{home}"
      end if
    end tell
    delay 1.6
    tell application "System Events"
      if not (UI elements enabled) then return "NOACCESS"
      repeat with c in characters of {quote_applescript(query)}
        keystroke (c as text)
        delay 0.045
      end repeat
      delay 0.3
      key code 36
    end tell
    return "OK"
    """
    result = osascript(script, timeout=60)
    if result.strip() == "OK":
        return None
    if "NOACCESS" in result or "1719" in result or "assistive" in result.lower():
        return (
            "Live typing needs Accessibility permission: System Settings > "
            "Privacy & Security > Accessibility, and enable your terminal app"
        )
    return result or "typing failed"
