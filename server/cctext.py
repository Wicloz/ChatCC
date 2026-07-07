"""Convert arbitrary Unicode chat text into CC's 8-bit-safe character set.

CC:Tweaked terminals have no Unicode support: a Lua string is raw bytes, one
byte renders as exactly one glyph from a fixed 256-glyph font. That font's
upper range (0xA0-0xFF) matches ISO-8859-1 (Latin-1 Supplement) — confirmed
against the CC font chart and CC:Tweaked's own docs — so accented Western
European text (café, naïve, façade) has a free, lossless, native glyph.
Everything else (emoji, CJK, Cyrillic, Arabic, ...) has no glyph at all.

This module:
  - converts emoji to :shortcode: form (e.g. "\U0001F602" -> ":joy:"), matching
    the look of channel emotes and keeping their meaning instead of dropping them
  - passes ASCII and Latin-1 Supplement characters through unchanged
  - collapses any *run* of anything else CC can't render to at most three '?',
    so a foreign-script sentence or an emoji cluster our list doesn't know still
    reads as "something was here" rather than one '?' per lost codepoint

Only the Latin-1 supplement is treated as native; wider transliteration (e.g.
romanizing CJK/Cyrillic) is out of scope — English-language streams are the
target for now, and script-guessing transliterators are unreliable besides
(e.g. they cannot distinguish Chinese from Japanese for shared Han characters).

Scope note: this only handles single-codepoint text. It deliberately does not
handle right-to-left reordering, combining diacritics stacking, or other
complex text shaping — irrelevant once non-Latin1 runs are collapsed to '?'.
"""

import emoji

# CC's printable range: ASCII (0x20-0x7E) plus Latin-1 Supplement (0xA0-0xFF).
# 0x7F and 0x80-0x9F are CC's own control/graphics glyphs, not text, and must
# not appear here (matches the CCMF spec's text-safety definition).
_ASCII_MIN, _ASCII_MAX = 0x20, 0x7E
_LATIN1_MIN, _LATIN1_MAX = 0xA0, 0xFF

MAX_PLACEHOLDER = 3  # a run of unencodable characters never becomes more than this


def _is_cc_native(ch: str) -> bool:
    cp = ord(ch)
    return (_ASCII_MIN <= cp <= _ASCII_MAX) or (_LATIN1_MIN <= cp <= _LATIN1_MAX)


def to_cc_text(text: str) -> str:
    if not text:
        return ""
    text = emoji.demojize(text, delimiters=(":", ":"))

    out = []
    run = 0
    for ch in text:
        if _is_cc_native(ch):
            if run:
                out.append("?" * min(run, MAX_PLACEHOLDER))
                run = 0
            out.append(ch)
        else:
            run += 1
    if run:
        out.append("?" * min(run, MAX_PLACEHOLDER))
    return "".join(out)
