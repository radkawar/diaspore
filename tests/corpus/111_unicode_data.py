import unicodedata as ucd


def points(text):
    return [ord(char) for char in text]


for text in ("\ud800\udfff", "\U000103ff", f"\ud800\udfff{1}", f"\U000103ff{1}"):
    print("literal points", points(text))


print("version", ucd.unidata_version)
for code in (
    0, 9, 32, 40, 65, 97, 0xA0, 0xBD, 0xE9, 0x1C5,
    0x301, 0x315, 0x323, 0x345, 0x378, 0x5D0, 0x627, 0x660,
    0x6F9, 0xF33, 0x200D, 0x202E, 0x2066, 0x20AC, 0x2167,
    0x2211, 0x2468, 0x4E00, 0xAC00, 0xD7A3, 0xD800, 0xDFFF,
    0xE000, 0xFDD0, 0xFEFF, 0xFF10, 0xFF21, 0xFFFF,
    0x10107, 0x1D165, 0x1D2C0, 0x1D2D3, 0x1D7D9, 0x1E4D0,
    0x1F600, 0x1FAE8, 0x20000, 0x31350, 0x323AF, 0xE0100, 0x10FFFF,
):
    char = chr(code)
    print("properties", code, ucd.name(char, None), ucd.category(char),
          ucd.combining(char), ucd.bidirectional(char), ucd.east_asian_width(char),
          ucd.mirrored(char), ucd.decimal(char, None), ucd.digit(char, None),
          ucd.numeric(char, None), ucd.decomposition(char))
    primary = ucd.name(char, None)
    if primary is not None:
        print("roundtrip", code, ucd.lookup(primary) == char)

for label in (
    "LATIN SMALL LETTER E WITH ACUTE", "latin small letter e with acute",
    "SNOWMAN", "GRINNING FACE", "NULL", "LF", "BYTE ORDER MARK",
    "LATIN CAPITAL LETTER GHA", "WEIERSTRASS ELLIPTIC FUNCTION", "VS256",
    "KEYCAP DIGIT ONE", "LATIN SMALL LETTER A WITH MACRON AND GRAVE",
    "TAMIL SYLLABLE SAI", "HANGUL SYLLABLE GA", "HANGUL SYLLABLE A",
    "HANGUL SYLLABLE GAG", "HANGUL SYLLABLE HIH",
    "CJK UNIFIED IDEOGRAPH-4E00", "CJK UNIFIED IDEOGRAPH-04E00",
    "CJK UNIFIED IDEOGRAPH-31350", "CJK COMPATIBILITY IDEOGRAPH-F900",
    "cjk compatibility ideograph-f900", "NUSHU CHARACTER-1B170",
    "KHITAN SMALL SCRIPT CHARACTER-18B00",
):
    print("lookup", label, points(ucd.lookup(label)))
print("bytes name", points(ucd.lookup(b"LATIN CAPITAL LETTER A")))

for label in (
    "NOT A UNICODE NAME", "", " LATIN CAPITAL LETTER A", "LATIN CAPITAL LETTER A ",
    "LATIN CAPITAL LETTER A\x00", "LATIN \u017fMALL LETTER A",
    "hangul syllable ga", "HANGUL SYLLABLE ga", "HANGUL SYLLABLE GAX",
    "cjk unified ideograph-4e00", "CJK UNIFIED IDEOGRAPH-4e00",
    "CJK UNIFIED IDEOGRAPH-323B0", "CJK UNIFIED IDEOGRAPH-004E00",
    "NUSHU CHARACTER-1B300", "A" * 257,
):
    try:
        ucd.lookup(label)
    except KeyError as exc:
        print("missing name", repr(label), str(exc))

for form in ("NFC", "NFD", "NFKC", "NFKD"):
    for text in (
        "", "plain ASCII", "\u00e9", "e\u0301", "\u212b", "\u1e69",
        "s\u0307\u0323", "\u0301\u0323A", "A\u0305\u0301", "A\u030a\u0301",
        "A\u030a\u034f\u0301", "\u0344", "\u0f73", "\u0958", "\u09cb",
        "\u1100\u1161\u11a8", "\uac00\u11a8", "\uac01", "\ud7a3",
        "\u1100\u0301\u1161", "\ufb03", "\u00bc", "\uff21\u2460",
        "\u00a0\u2126\u212a", "\ufdfa", "\U0001d400", "\U0001d15e",
        "\U0002f800", "\U0001f600\U0001e4d0", "\ud800\udfff\uffff",
    ):
        normalized = ucd.normalize(form, text)
        print("normalize", form, points(text), points(normalized),
              ucd.is_normalized(form, text), ucd.is_normalized(form, normalized),
              ucd.normalize(form, normalized) == normalized)

for function in (ucd.name, ucd.decimal, ucd.digit, ucd.numeric):
    marker = []
    print("default identity", function("\u0378", marker) is marker,
          function("\u0378", None) is None)
    try:
        function("\u0378")
    except ValueError as exc:
        print("missing property", str(exc))

for function in (ucd.name, ucd.category, ucd.combining, ucd.bidirectional,
                 ucd.east_asian_width, ucd.mirrored, ucd.decimal, ucd.digit,
                 ucd.numeric, ucd.decomposition):
    for invalid in ("", "ab", 1, b"A", None):
        try:
            function(invalid)
        except TypeError:
            print("character type error", repr(invalid))

for function in (ucd.normalize, ucd.is_normalized):
    for form, text in (("nfc", "a"), ("NFK", "a"), ("NFC\x00", "a")):
        try:
            function(form, text)
        except ValueError as exc:
            print("form error", str(exc))
    for form, text in ((None, ""), ("NFC", b"a"), ("NFD", None)):
        try:
            function(form, text)
        except TypeError:
            print("normalization type error")
print("empty invalid form", repr(ucd.normalize("invalid", "")), ucd.is_normalized("invalid", ""))

for text in (
    "", "_", "__", "a", "Z", "a0", "_0", "0a", "class", "None",
    "a b", "a-b", "a.b", "a\x00b", "\u00e9", "e\u0301", "\u0301e",
    "\u03b1\u03b2", "\u4e00", "\u00b2", "a\u00b2", "\u00b7", "a\u00b7",
    "\u1885", "\u2118", "\u212e", "\u309b", "\u037a", "\u0e33",
    "a\u200c", "a\u200d", "\uff21\uff19", "\u2167", "\ufb03",
    "\U00010400", "\U0001d400", "\U00011f02", "\U0001e4d0",
    "\U00031350", "\U0001f600", "a\U0001f600", "\ud800", "a\udfff",
    "\uffff", "\U0010ffff",
):
    print("identifier", points(text), text.isidentifier())
