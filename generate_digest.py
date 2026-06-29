#!/usr/bin/env python3
"""Generate the daily "What Is Cas Reading" digest using Claude with web search.

Runs once per day. It:
  1. Reads the editorial brief from custom_prompt.txt
  2. Asks Claude (with the web_search tool) to compile today's digest as JSON
  3. Writes data/<YYYY-MM-DD>.json
  4. Rebuilds data/index.json and prunes digests older than RETENTION_DAYS

The Anthropic API key is read from the ANTHROPIC_API_KEY environment variable.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

MODEL = "claude-sonnet-4-6"
EFFORT = "medium"  # thinking depth / token spend: low | medium | high | max
MAX_WEB_SEARCHES = 50  # cap server-side web searches per run (each costs ~$0.01)
RETENTION_DAYS = 60
DEDUP_LOOKBACK_DAYS = 7  # how many prior days of digests to show Claude to avoid repeats
EASTERN = ZoneInfo("America/New_York")

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
PROMPT_FILE = ROOT / "custom_prompt.txt"

# JSON shape we require back. Kept in the script (not custom_prompt.txt) so editing
# the editorial brief can't accidentally break parsing.
OUTPUT_INSTRUCTIONS = """
================================================================================
OUTPUT FORMAT — READ CAREFULLY
================================================================================
After you finish searching, respond with EXACTLY ONE JSON object and NOTHING else
(no prose before or after, no markdown code fences). It must match this schema:

{
  "entries": [
    {
      "tag": "Paper",
      "title": "Short specific headline for this topic",
      "summary": "One plain sentence: what happened and why it matters.",
      "date": "2026-06-28",
      "links": [
        {"title": "Source or article title", "url": "https://..."}
      ]
    }
  ]
}

Rules:
- 10-20 entries on a normal day; fewer is fine on a slow day. One entry per story.
- "date" is the item's actual publication/filing/release date as YYYY-MM-DD, taken from
  the search results (NOT today's date, NOT a guess). It must be within the last 7 days.
  If you genuinely could not find a date, use an empty string "" — never fabricate one.
- Include only the 2-3 BEST links per entry (primary source first). Never more than 3.
- Every entry MUST have a "tag" that is EXACTLY ONE of: "Paper", "Policy",
  "Lawsuit", "News", "Misc". Choose the best fit:
    - "Paper"   = academic papers / preprints (e.g. arXiv).
    - "Policy"  = bills, legislation, executive/regulatory actions, official
                  government guidance and reports.
    - "Lawsuit" = court filings, complaints, rulings, settlements, enforcement.
    - "News"    = news articles, incidents, and org/company updates.
    - "Misc"    = anything that doesn't clearly fit the above.
- Every URL must be a real link you found via web search. Never invent one.
- Order entries roughly by importance to Cas (most important first).
- Output ONLY the JSON object.
"""


def recently_covered(today_str: str) -> str:
    """Titles + URLs from the last few days' digests, so Claude can avoid repeats."""
    cutoff = datetime.now(EASTERN).date() - timedelta(days=DEDUP_LOOKBACK_DAYS)
    lines = []
    for path in sorted(DATA_DIR.glob("*.json"), reverse=True):
        if path.name == "index.json" or path.stem == today_str:
            continue
        try:
            d = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for entry in data.get("entries", []):
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            urls = ", ".join(
                l.get("url", "") for l in entry.get("links", []) if l.get("url")
            )
            lines.append(f"- [{path.stem}] {title}" + (f" ({urls})" if urls else ""))

    if not lines:
        return ""
    return (
        "\n"
        "================================================================================\n"
        "ALREADY COVERED IN THE LAST WEEK — DON'T REPEAT VERBATIM\n"
        "================================================================================\n"
        "These stories, papers, and bills already appeared in the last week's digests. Don't\n"
        "re-run the same item with the same framing. You MAY cover one again if there is a\n"
        "genuinely new, substantive development this week (a ruling, a new stage, a new day of\n"
        "an ongoing event) — in that case make the title and summary about WHAT IS NEW. Match\n"
        "on the URL as well as the title, since the same item may be phrased differently.\n\n"
        + "\n".join(lines)
        + "\n"
    )


def build_prompt() -> str:
    brief = PROMPT_FILE.read_text(encoding="utf-8")
    today_str = datetime.now(EASTERN).strftime("%Y-%m-%d")
    today = datetime.now(EASTERN).strftime("%A, %B %-d, %Y")
    return (
        f"Today is {today} (US Eastern Time).\n\n"
        f"{brief}\n{recently_covered(today_str)}\n{OUTPUT_INSTRUCTIONS}"
    )


def run_agent(client: anthropic.Anthropic, prompt: str) -> str:
    """Run the web-search agent loop until Claude produces its final text answer."""
    # Use the BASIC web_search variant (not web_search_20260209). The newer
    # variant's "dynamic filtering" runs server-side code execution in a container,
    # which makes pause_turn continuations fragile and slow: the code-execution
    # blocks cross-reference web_search blocks across turns, intermittently causing
    # 400s ("container_id is required ...", "source tool ... not found") and, when
    # worked around, very long multi-round loops. This digest only gathers links,
    # so it gains nothing from dynamic filtering. The basic variant does no code
    # execution, so pause_turn resumes cleanly with the documented [user, assistant]
    # pattern below.
    tools = [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_WEB_SEARCHES}
    ]
    # Cache the (large, stable) editorial brief so each continuation round reads it
    # from cache (~0.1x cost) instead of reprocessing it at full price.
    user_content = [
        {"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}
    ]
    messages = [{"role": "user", "content": user_content}]

    for _ in range(15):  # safety cap on continuation rounds
        # Stream the response. A web-search + thinking turn can run long, and the SDK
        # refuses NON-streaming requests it estimates may exceed 10 minutes
        # ("Streaming is required for operations that may take longer than 10 minutes").
        # We don't need the incremental tokens, so we just collect the final message.
        with client.messages.stream(
            model=MODEL,
            max_tokens=24000,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            # Auto-cache the last block too, so accumulated search results carry over
            # across rounds within this run (the 5-min cache TTL covers a single run).
            cache_control={"type": "ephemeral"},
            tools=tools,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        # Server-side tool loop hit its internal limit; re-send to continue. With the
        # basic web_search variant the response is self-contained, so resetting to
        # [user, assistant] (the documented pause_turn pattern) is correct.
        if response.stop_reason == "pause_turn":
            messages = [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": response.content},
            ]
            continue

        text = "".join(b.text for b in response.content if b.type == "text")
        if not text.strip():
            # No text block in the final turn. Surface why instead of returning ""
            # (which downstream would hit as an unhelpful empty-JSON parse error).
            block_types = sorted({b.type for b in response.content})
            raise RuntimeError(
                f"Web-search agent produced no text (stop_reason={response.stop_reason}, "
                f"blocks={block_types}). Likely truncated before the JSON answer."
            )
        return text

    raise RuntimeError("Web-search agent did not finish within the round limit.")


def extract_json(text: str) -> dict:
    """Pull the JSON object out of the model's reply, tolerating stray fences/prose."""
    cleaned = text.strip()
    # Strip ```json ... ``` fences if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    # Fall back to the outermost { ... } span.
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1:
            cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


MAX_LINKS_PER_ENTRY = 3


def normalize_entries(entries: list) -> list:
    """Enforce the per-entry link cap and keep an optional ISO date field."""
    for entry in entries:
        links = entry.get("links")
        if isinstance(links, list) and len(links) > MAX_LINKS_PER_ENTRY:
            entry["links"] = links[:MAX_LINKS_PER_ENTRY]
        # Keep "date" only if it's a non-empty string; drop anything malformed.
        d = entry.get("date")
        if not (isinstance(d, str) and d.strip()):
            entry.pop("date", None)
    return entries


def write_digest(date_str: str, entries: list) -> Path:
    payload = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entries": entries,
    }
    out = DATA_DIR / f"{date_str}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def rebuild_index_and_prune() -> list:
    """Delete digests older than RETENTION_DAYS and rewrite data/index.json."""
    cutoff = datetime.now(EASTERN).date() - timedelta(days=RETENTION_DAYS)
    dates = []
    for path in DATA_DIR.glob("*.json"):
        if path.name == "index.json":
            continue
        try:
            d = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            path.unlink()
            continue
        dates.append(path.stem)

    dates.sort(reverse=True)  # newest first
    (DATA_DIR / "index.json").write_text(
        json.dumps({"digests": dates}, indent=2), encoding="utf-8"
    )
    return dates


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY is not set.")

    DATA_DIR.mkdir(exist_ok=True)
    client = anthropic.Anthropic()
    date_str = datetime.now(EASTERN).strftime("%Y-%m-%d")

    print(f"Generating digest for {date_str} ...")
    prompt = build_prompt()

    entries = None
    last_error = None
    last_reply = ""
    for attempt in range(1, 4):  # transient empty/truncated turns: retry the whole run
        reply = ""
        try:
            reply = run_agent(client, prompt)
            data = extract_json(reply)
            entries = data["entries"]
            assert isinstance(entries, list) and entries
            break
        except (ValueError, KeyError, AssertionError, RuntimeError) as e:
            last_error = e
            last_reply = reply
            print(f"Attempt {attempt}/3 failed: {e}")

    if entries is None:
        print("\nGiving up. Last raw reply follows:\n")
        print(last_reply)
        sys.exit(f"Parse error: {last_error}")

    entries = normalize_entries(entries)
    out = write_digest(date_str, entries)
    print(f"Wrote {len(entries)} entries to {out}")

    kept = rebuild_index_and_prune()
    print(f"Index rebuilt: {len(kept)} digest(s) retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
