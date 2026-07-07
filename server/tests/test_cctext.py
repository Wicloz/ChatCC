import pytest

from cctext import to_cc_text

# Cyrillic "Privet" and CJK "ni hao shi jie ma" (5 chars), by escape to avoid
# any ambiguity about the literal bytes in this source file.
CYRILLIC_A = "А"          # А
CJK_5 = "你好世界吗"  # 你好世界吗


def test_ascii_passthrough_unchanged():
    assert to_cc_text("Hello, world! 123") == "Hello, world! 123"


def test_empty_string():
    assert to_cc_text("") == ""


@pytest.mark.parametrize("text", [
    "café", "naïve", "façade", "Über", "Zürich", "El Niño",
])
def test_latin1_supplement_passthrough_unchanged(text):
    # CC's font matches ISO-8859-1 in this range -- no loss, no placeholder.
    assert to_cc_text(text) == text
    # Every character must be independently encodable as a single Latin-1 byte.
    to_cc_text(text).encode("latin-1")


def test_single_emoji_becomes_shortcode():
    assert to_cc_text("Great job! \U0001F525") == "Great job! :fire:"


def test_multi_codepoint_emoji_collapses_to_one_shortcode():
    # A ZWJ / skin-tone cluster must not be split into multiple '?' runs.
    out = to_cc_text("nice \U0001F44D\U0001F3FD")  # thumbs up + medium skin tone
    assert "?" not in out
    assert out.startswith("nice :thumbs_up")


def test_unencodable_run_capped_at_three_question_marks():
    out = to_cc_text("hello " + CJK_5 + " bye")  # 5 CJK chars
    assert out == "hello ??? bye"


def test_single_unencodable_char_is_one_question_mark():
    assert to_cc_text("a" + CYRILLIC_A + "b") == "a?b"


def test_mixed_emoji_and_unencodable_and_latin1():
    out = to_cc_text("café \U0001F600 " + CJK_5)
    assert out == "café :grinning_face: ???"


def test_control_characters_collapse_to_placeholder():
    assert to_cc_text("a\nb") == "a?b"
    assert to_cc_text("a\tb") == "a?b"


def test_c1_range_not_treated_as_native():
    # 0x80-0x9F are CC's own graphics glyphs, not text -- must not pass through.
    assert to_cc_text("ab") == "a?b"


def test_del_not_treated_as_native():
    assert to_cc_text("ab") == "a?b"


def test_consecutive_separate_runs_each_capped():
    out = to_cc_text(CJK_5 + CJK_5 + " hi " + CJK_5 + CJK_5)
    assert out == "??? hi ???"
