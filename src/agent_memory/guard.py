# src/agent_memory/guard.py
"""Secrets never enter a store.

A memory is plain text that comes back on every retrieval, to every agent that asks — so a key, token
or password saved once is repeated forever. `find_secret` names what a text LOOKS LIKE it carries;
`memory_save` refuses the whole text on a hit (never redacts: a redacted memory is a lie about what was
said) and logs the KIND, never the text. The shapes are the recognisable prefixes credential issuers use,
a password-bearing connection URL, and — for everything without a prefix — a long run whose character
distribution is too flat to be a word, a path or an identifier.

Known limit, deliberate: a secret written as prose ("the wifi password is sunflower42") has no shape to
recognise and passes. The instruction line the harness carries ("never save passwords, keys or tokens")
is the layer that covers it; this module is the second layer, not the only one.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Secret:
    kind: str       # a stable, log-safe name — this is all the invocation log ever records
    why: str        # the plain-language half of the refusal message


@dataclass(frozen=True)
class _Kind:
    kind: str
    why: str
    pattern: re.Pattern


# Specific shapes first, the generic entropy measure last: a real GitHub token is also a high-entropy run, and
# the specific name is the one the agent can act on.
KINDS: tuple[_Kind, ...] = (
    # The left boundary keeps a kebab identifier whose word ends in sk (task-, disk-, risk-, desk-) from reading as
    # a key; a real key is never preceded by a letter or digit.
    _Kind("api_key_openai", "an OpenAI-style API key (sk-…)", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")),
    _Kind("github_token", "a GitHub token (ghp_/gho_/ghu_/ghs_/ghr_ or github_pat_)",
          re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{60,}")),
    _Kind("aws_access_key", "an AWS access key id (AKIA…)", re.compile(r"AKIA[0-9A-Z]{16}")),
    _Kind("private_key_block", "a PEM private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    _Kind("jwt", "a JWT (three base64url segments)",
          re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    _Kind("slack_token", "a Slack token (xox…)", re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}")),
    _Kind("connection_string_with_password", "a connection URL carrying a password (scheme://user:password@host)",
          re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:]+:[^@\s]+@")),
    _Kind("high_entropy_token", "a long high-entropy token (32+ characters at 4.0+ bits per character)",
          re.compile(r"[A-Za-z0-9+/=_-]{32,}")),
)

ENTROPY_BITS_PER_CHAR = 4.0       # the flatness a 32+ run must reach to count as a token rather than a word
_HEX_RUN = re.compile(r"[0-9a-f]+|[0-9A-F]+")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_SEPARATORS = re.compile(r"[/_=-]+")     # path, kebab, snake, and the = of KEY=value (base64 pads with = only at the end)
# One segment of a path or identifier: a word (any letter case, so camelCase counts), a number, a word with a
# short numeric suffix or prefix (v2, py313, 27B, pre2000), or a hex group (a short sha inside a path). A random
# token interleaves digits with letters; a word carries them at one end, if at all. Known limit: a segment with
# letters on BOTH sides of a digit group (a hardware model name such as x870e) is not word-like here, and a 32+
# run containing one is judged by entropy alone.
_WORD_LIKE = re.compile(r"[A-Za-z]+|[0-9]+|[A-Za-z]+[0-9]{1,4}|[0-9]{1,4}[A-Za-z]+|[0-9a-f]+|[0-9A-F]+")
_NO_DIGIT_NO_PAD = re.compile(r"[A-Za-z/_-]+")     # letters and separators only: a word or identifier, never a token
MIN_JOINED_SEGMENTS = 3          # the joined shape needs two separators; a random token rarely has them
# A hex segment this long is a sha or a digest when it sits in a PATH (runs/<sha>/summary.md) — and the body of a
# structured token when it is joined by _ or - alone (prefix_v1_<64 hex>, xapp-1-A…-…-<64 hex>). Only a path may be
# rescued by one; measured on shaped fakes, the unbounded form hid every such token.
LONG_HEX_SEGMENT = 32
_HEX_SEGMENT = re.compile(r"[0-9a-f]+|[0-9A-F]+")


def shannon_bits_per_char(run: str) -> float:
    """Shannon entropy of the character distribution, in bits per character. A run of one repeated character
    is 0.0; a run in which every character is distinct is log2(len)."""
    if not run:
        return 0.0
    counts: dict[str, int] = {}
    for ch in run:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(run)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _is_identifier_shaped(run: str) -> bool:
    """What the entropy measure must NOT read as a token, because it is an identifier by structure:

    - a hex-only run (a memory id, a commit sha, a digest) or a UUID. Sixteen symbols cannot exceed 4.0 bits
      per character, so the entropy test could only ever catch the perfectly uniform hex string — and that one
      is as likely an id as a key. Named out explicitly so the behaviour is a rule, not an arithmetic accident.
    - a run of letters and separators with no digit and no base64 padding: a long camelCase type name, a
      snake_case test name, a path of words. A random token of 32+ characters carries a digit or a `+`/`=` all
      but always (letters-only base64 at 32 characters happens about once in a thousand tokens).
    - a path, a dated file name, a branch name or an env-var assignment: words, numbers and hex groups joined by
      / - _ = , at least three of them. Measured on real dev-journal saves, the long ones sit at 4.0-4.6 bits per
      character (many distinct letters, digits from a date, the separators themselves) — the entropy alone would
      refuse a tenth of what agents actually save. A random token rarely takes this shape: its digits sit INSIDE
      the letter runs, and it seldom carries two separators. Measured on random 32-character base64url tokens,
      this exclusion hides under one in a hundred; the specific kinds above catch the prefixed ones regardless."""
    if _HEX_RUN.fullmatch(run) or _UUID.fullmatch(run) or _NO_DIGIT_NO_PAD.fullmatch(run):
        return True
    segments = [seg for seg in _SEPARATORS.split(run) if seg]
    is_path = "/" in run
    return len(segments) >= MIN_JOINED_SEGMENTS and all(_segment_is_word_like(seg, is_path) for seg in segments)


def _segment_is_word_like(seg: str, is_path: bool) -> bool:
    if not _WORD_LIKE.fullmatch(seg):
        return False
    if len(seg) >= LONG_HEX_SEGMENT and _HEX_SEGMENT.fullmatch(seg) and any(ch.isdigit() for ch in seg):
        return is_path          # a long hex group is a word only inside a path
    return True


def find_secret(text: str | None) -> Secret | None:
    """The first recognisable secret shape in `text`, or None. Specific kinds are tried in order before the
    generic high-entropy measure; the result carries the kind (for the log) and the why (for the message)."""
    if not text:
        return None
    for k in KINDS:
        if k.kind == "high_entropy_token":
            for m in k.pattern.finditer(text):
                run = m.group(0)
                if _is_identifier_shaped(run):
                    continue
                if shannon_bits_per_char(run) >= ENTROPY_BITS_PER_CHAR:
                    return Secret(k.kind, k.why)
        elif k.pattern.search(text):
            return Secret(k.kind, k.why)
    return None


def find_secret_in_save(text: str | None, thread: str | None = None, scope: str | None = None,
                        facets: dict | None = None, save_concepts: list | None = None) -> tuple[Secret, str] | None:
    """Every agent-supplied string of a save, not only the text: a token in a thread label, a scope, a facet value or
    a concept label is stored and repeated just the same. Returns (secret, field) for the first hit, field one of
    text | thread | scope | facet | concept — a fixed name, never the value or the key the agent supplied — or None.
    Only strings are looked at; a malformed shape is the downstream validation's business, as before."""
    for field, value in (("text", text), ("thread", thread), ("scope", scope)):
        if isinstance(value, str) and (s := find_secret(value)) is not None:
            return s, field
    if isinstance(facets, dict):
        for key, value in facets.items():
            for candidate in (key, value):
                if isinstance(candidate, str) and (s := find_secret(candidate)) is not None:
                    return s, "facet"
    if isinstance(save_concepts, (list, tuple)):
        for pair in save_concepts:
            parts = pair if isinstance(pair, (list, tuple)) else (pair,)
            for candidate in parts:
                if isinstance(candidate, str) and (s := find_secret(candidate)) is not None:
                    return s, "concept"
    return None


def kind_in_field(secret: Secret, field: str) -> str:
    """The kind as the reply and the log carry it: bare for the text (the primary contract), `<kind> in <field>`
    for every other field, so the agent knows which argument to fix."""
    return secret.kind if field == "text" else f"{secret.kind} in {field}"


def refusal_message(secret: Secret, field: str = "text") -> str:
    what = {"facet": "facet", "concept": "concept label"}.get(field, field)
    return (f"refused: the {what} contains what looks like {secret.why}; memories are plain text and come back "
            f"on every retrieval; store the secret elsewhere and save the pointer")
