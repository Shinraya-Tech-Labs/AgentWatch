"""Unit tests for Steganography & Covert Channel Neutralizer (arXiv:2606.28425)."""

from panoptis.protect.stego_neutralizer import StegoNeutralizer


def test_stego_neutralizer_strips_trailing_whitespace_modulation():
    neutralizer = StegoNeutralizer()
    # Construct text carrying 24 bits of covert whitespace binary modulation
    # (spaces = 0, tabs = 1 at ends of lines)
    covert_text = (
        "def process_data():   \t\n"
        "    result = query_db() \t \n"
        "    return result    \t\t\n"
        "print('Done')  \t  \n"
    )

    res = neutralizer.neutralize(covert_text)
    assert res.had_covert_channel
    assert res.bits_neutralized_est >= 16

    # Verify all trailing spaces/tabs are stripped
    for line in res.cleaned_payload.splitlines():
        assert line == line.rstrip(" \t")


def test_stego_neutralizer_strips_zero_width_and_tag_characters():
    neutralizer = StegoNeutralizer()
    # Zero-width spaces & joiners embedded inside words
    text_with_zero_width = "Hello\u200bWorld\u200cfrom\u200dAgent\ufeffWatch"
    res = neutralizer.neutralize(text_with_zero_width)

    assert res.cleaned_payload == "HelloWorldfromAgentWatch"
    assert res.had_covert_channel
    assert any(f.code == "STEGO_INVISIBLE_CHARS_STRIPPED" for f in res.findings)


def test_stego_neutralizer_rfc8785_canonical_json():
    neutralizer = StegoNeutralizer()
    # Non-canonical dictionary with permuted keys and float variations
    data = {
        "zeta": 1.0,
        "alpha": {"beta": "value  \t", "gamma": 42},
        "delta": [3, 2, 1],
    }

    res = neutralizer.neutralize(data)
    # Check that keys are sorted and inner string whitespace is cleaned
    assert list(res.cleaned_payload.keys()) == ["alpha", "delta", "zeta"]
    assert res.cleaned_payload["alpha"]["beta"] == "value"

    canonical_json = neutralizer.to_canonical_json_string(data)
    expected_prefix = '{"alpha":{"beta":"value  \\t","gamma":42}'
    assert canonical_json.startswith(expected_prefix)
    # separators must be strictly (',', ':') with no extra whitespace
    assert ": " not in canonical_json
    assert ", " not in canonical_json


def test_stego_neutralizer_unicode_nfkc_normalization():
    neutralizer = StegoNeutralizer()
    # Half-width / full-width and compatibility characters
    fullwidth_text = "ＡｇｅｎｔＷａｔｃｈ"  # Full-width characters
    res = neutralizer.neutralize(fullwidth_text)
    assert res.cleaned_payload == "AgentWatch"
