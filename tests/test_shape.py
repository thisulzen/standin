import itertools
import string

from standin.shape import permute, restyle

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))


def test_permute_is_one_to_one_on_every_small_domain():
    # Sizes either side of perfect squares exercise the cycle walking hardest.
    for n in [1, 2, 3, 10, 15, 16, 17, 26, 99, 100, 101, 676, 1000]:
        assert sorted(permute(x, n, KEY, b"t") for x in range(n)) == list(range(n))


def test_permute_depends_on_key_and_tweak():
    base = [permute(x, 1000, KEY, b"t") for x in range(1000)]
    assert base != [permute(x, 1000, OTHER_KEY, b"t") for x in range(1000)]
    assert base != [permute(x, 1000, KEY, b"u") for x in range(1000)]
    assert base != list(range(1000))


def test_restyle_keeps_length_case_and_punctuation():
    for value in ["C000123", "jane.smith@example.com", "+61 412 345 678", "AB-12/cd", "Zoë O'Brien"]:
        out = restyle(value, KEY)
        assert len(out) == len(value)
        for a, b in zip(value, out):
            if a in string.digits:
                assert b in string.digits
            elif a in string.ascii_uppercase:
                assert b in string.ascii_uppercase
            elif a in string.ascii_lowercase:
                assert b in string.ascii_lowercase
            else:
                assert a == b
        assert out != value


def test_restyle_is_deterministic_and_keyed():
    assert restyle("C000123", KEY) == restyle("C000123", KEY)
    assert restyle("C000123", KEY) != restyle("C000123", OTHER_KEY)


def test_restyle_never_gives_two_values_the_same_stand_in():
    values = ["".join(p) for p in itertools.product(string.ascii_uppercase, string.digits)]
    outs = [restyle(v, KEY) for v in values]
    assert len(set(outs)) == len(values) == 260


def test_same_digits_with_different_punctuation_are_unrelated():
    assert restyle("12-34", KEY).replace("-", "") != restyle("12/34", KEY).replace("/", "")


def test_values_with_nothing_to_restyle_come_back_unchanged():
    for value in ["", "-", "  ", "王小明"]:
        assert restyle(value, KEY) == value


def test_last_letter_of_a_long_value_changes_the_whole_stand_in():
    # A long value needs more round-function output than one HMAC block. If the
    # output weren't stretched, the front of the stand-in would barely change.
    a = restyle("a" * 300, KEY)
    b = restyle("a" * 299 + "b", KEY)
    same = sum(x == y for x, y in zip(a, b))
    assert same < 60  # about 300/26 expected by chance
