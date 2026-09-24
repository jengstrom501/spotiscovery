"""Optional: Claude reads your taste profile and proposes genre blends + artists.
Every artist it names is checked against Last.fm, so invented bands never reach the page."""
import json
import os

import store

DEFAULT_MODEL = "claude-opus-5"

SCHEMA = {
    "type": "object",
    "properties": {"blends": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "why": {"type": "string"},
            "artists": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "note": {"type": "string"}},
                "required": ["name", "note"], "additionalProperties": False}},
        },
        "required": ["name", "tags", "why", "artists"], "additionalProperties": False}}},
    "required": ["blends"], "additionalProperties": False,
}

PROMPT = """You're a music obsessive helping a friend find artists they've overlooked.
Here is their listening profile (weights are relative listening time):

{profile}

Propose 5 blends. Each blend combines 2-3 sub-genres or sonic traits from their profile in a way
they probably haven't explored, for example a tag they love crossed with one they only dabble in, or
the direction their taste drift is heading. For each blend, give a short evocative name, the tags
it mixes, one or two sentences on why it fits them specifically, and 6 real artists that sit in
that blend, each with a one-line note on what to listen for.

Prefer lesser-known artists. Do not name anyone in their top artists or already-recommended list.
Only name artists you are confident exist."""


def available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def suggest(profile, known, lf):
    import anthropic  # optional dependency, only needed here

    model = os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)  # re-read so a saved key/model needs no restart
    client = anthropic.Anthropic()
    kwargs = {}
    if model.startswith(("claude-opus-5", "claude-fable-5")):
        # Server-side refusal fallback: a declined request is re-run on Anthropic's recommended model.
        kwargs = {"betas": ["server-side-fallback-2026-07-01"], "extra_body": {"fallbacks": "default"}}
    resp = client.beta.messages.create(
        model=model, max_tokens=16000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": PROMPT.format(profile=json.dumps(profile, indent=1))}],
        **kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off, try again")
    data = json.loads(next(b.text for b in resp.content if b.type == "text"))

    for b in data["blends"]:
        kept = []
        for a in b["artists"]:
            inf = lf.info(a["name"])
            if inf and store.norm(inf["name"]) not in known:
                kept.append({**a, "name": inf["name"], "listeners": inf["listeners"]})
        b["dropped"] = len(b["artists"]) - len(kept)  # hallucinated or already known
        b["artists"] = kept
    return data["blends"]
