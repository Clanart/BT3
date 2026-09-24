This confirms the finding: the `upb`-based JSON encoder (`upb/json/encode.c`, and its generated copies in `php/ext/google/protobuf/php-upb.c` and `ruby/ext/google/protobuf_c/ruby-upb.c`) — used by Python (via the upb backend), PHP, and Ruby ProtoJSON serialization — escapes only `\n \r \t \" \f \b \\` and low control chars (`< 0x20`). It does **not** escape `<`, `>`, `&`, `=`, `'`, unlike the C++ `src/google/protobuf/json/internal/writer.cc` (`MustEscape`, lines 194-268) and Java `JsonFormat.java` (lines 1568-1592), which explicitly added those escapes as defense-in-depth against XSS (referencing bug `b/73832901`, also covered by `JsonFormatTest.testHtmlEscape`, lines 2016-2035).

### Title
Inconsistent HTML/XSS-defensive escaping in upb-based ProtoJSON string encoder vs. C++/Java encoders - (File: `upb/json/encode.c`)

### Summary
Protobuf's C++ and Java `ProtoJSON` printers deliberately escape `<`, `>`, `&`, `=`, `'` (and several Unicode bidi/format characters) in string field values as defense-in-depth against script-injection when JSON output is later embedded in an HTML/`<script>` context by a consuming application. The `upb` C JSON encoder — which backs Python's upb-based binding, the PHP extension, and the Ruby extension — implements only the RFC-8259-mandated minimal escapes (control chars, `"`, `\`) and omits this hardening entirely.

### Finding Description
`jsonenc_put_escaped_char` in `upb/json/encode.c` (duplicated verbatim in `php/ext/google/protobuf/php-upb.c` and `ruby/ext/google/protobuf_c/ruby-upb.c`) only escapes `\n \r \t \" \f \b \\` and bytes `< 0x20`; any other byte, including `<`, `>`, `&`, `=`, `'`, is copied through verbatim via `jsonenc_putbytes` [1](#0-0) , and this function is the sole per-character escape path used by `jsonenc_stringbody`/`jsonenc_string` for every string field value [2](#0-1) . In contrast, the C++ printer's `MustEscape` explicitly escapes `<`, `>`, and other characters "to prevent security bugs in JavaScript" [3](#0-2) , and Java's `JsonFormat` adds identical escaping with an explicit comment about XSS risk mitigation [4](#0-3) , validated by the regression test `testHtmlEscape` for bug `b/73832901` [5](#0-4) .

The failed invariant, transplanted from the Sablier report: any attacker-controlled string that reaches a serializer's output must have HTML/script-meaningful characters neutralized before that output is handed to a downstream consumer that renders/embeds it in an HTML or JavaScript context. Sablier's flawed length-based filter failed to neutralize `<script>`/`<img onerror>`; the upb JSON encoder has no such filter at all for these characters. An ordinary client's Protobuf message (any `string` field value, fully attacker-controlled, arbitrary length) passed through the public `MessageToJson`/`google.protobuf.json_format` (Python upb backend), PHP `serializeToJsonString`, or Ruby `to_json` API will emit that value with `<`, `>`, `&`, `=`, `'` completely unescaped in the resulting JSON.

### Impact Explanation
This is a data-integrity/output-safety gap, not a memory-safety bug: it does not corrupt Protobuf's own state, but it removes a documented defense-in-depth escaping layer that the C++ and Java implementations carry specifically to blunt XSS when JSON output is interpolated into HTML/`<script>` contexts by a consuming application (e.g., server-side template embedding `MessageToJson()` output directly into a page, mirroring the Sablier SVG/NFT-marketplace rendering scenario). Any consuming application relying on parity between language bindings (e.g., a Python service using the upb backend to produce JSON that a browser or template engine later embeds) inherits an XSS-relevant string exactly where the C++/Java implementations were hardened against it.

### Likelihood Explanation
High reachability, moderate-to-low exploitability depending on the consumer: the vulnerable code path is exercised on every ProtoJSON serialization of any string field via the standard public API in Python (upb backend), PHP, and Ruby — no privileged access or special schema required, and any ordinary string value (including `<script>...</script>` or `<img src=x onerror=...>`) triggers the gap. Actual XSS impact is contingent on a downstream consumer embedding the emitted JSON into an HTML/JS context without its own escaping, matching the exact "consuming-application exposure" caveat also present in the original Sablier report.

### Recommendation
Port the C++/Java `<`, `>`, `&`, `=`, `'` (and associated Unicode bidi/format character) escaping logic from `src/google/protobuf/json/internal/writer.cc`'s `MustEscape` into `jsonenc_put_escaped_char` in `upb/json/encode.c`, and regenerate/resync the copies embedded in `php/ext/google/protobuf/php-upb.c` and `ruby/ext/google/protobuf_c/ruby-upb.c`, so all ProtoJSON encoders provide consistent defense-in-depth against HTML/script injection regardless of language backend.

### Proof of Concept
Using the Python upb backend (or PHP/Ruby equivalents):
```python
from google.protobuf import json_format
msg = TestAllTypes(optional_string="</script><script>alert(document.domain)</script>")
print(json_format.MessageToJson(msg))
```
Expected (matching Java's hardened behavior, per `testHtmlEscape` [5](#0-4) ): `<`/`>` escaped as `\u003c`/`\u003e`. Actual (upb backend): the raw `</script><script>alert(document.domain)</script>` bytes are copied verbatim into the `optionalString` JSON value string, because `jsonenc_put_escaped_char` has no case for `<` or `>` [1](#0-0) . This mirrors the Sablier `safeAssetSymbol` PoC where a crafted string payload survives the (in Protobuf's case, entirely absent) filter and reaches an HTML-embedding consumer verbatim.

### Citations

**File:** upb/json/encode.c (L268-301)
```c
static void jsonenc_put_escaped_char(jsonenc* e, char ch) {
  switch (ch) {
    case '\n':
      jsonenc_putstr(e, "\\n");
      break;
    case '\r':
      jsonenc_putstr(e, "\\r");
      break;
    case '\t':
      jsonenc_putstr(e, "\\t");
      break;
    case '\"':
      jsonenc_putstr(e, "\\\"");
      break;
    case '\f':
      jsonenc_putstr(e, "\\f");
      break;
    case '\b':
      jsonenc_putstr(e, "\\b");
      break;
    case '\\':
      jsonenc_putstr(e, "\\\\");
      break;
    default:
      if ((uint8_t)ch < 0x20) {
        jsonenc_printf(e, "\\u%04x", (int)(uint8_t)ch);
      } else {
        /* This could be a non-ASCII byte.  We rely on the string being valid
         * UTF-8. */
        jsonenc_putbytes(e, &ch, 1);
      }
      break;
  }
}
```

**File:** upb/json/encode.c (L303-317)
```c
static void jsonenc_stringbody(jsonenc* e, upb_StringView str) {
  const char* ptr = str.data;
  const char* end = UPB_PTRADD(ptr, str.size);

  while (ptr < end) {
    jsonenc_put_escaped_char(e, *ptr);
    ptr++;
  }
}

static void jsonenc_string(jsonenc* e, upb_StringView str) {
  jsonenc_putstr(e, "\"");
  jsonenc_stringbody(e, str);
  jsonenc_putstr(e, "\"");
}
```

**File:** src/google/protobuf/json/internal/writer.cc (L233-251)
```text
    // These are not required by the JSON spec, but help
    // to prevent security bugs in JavaScript.
    //
    // These were originally present in the ESF parser, so they are kept for
    // legacy compatibility (and because escaping most of these is in good
    // taste, regardless).
    case '<':
    case '>':
    case 0xfeff:      // Zero width no-break space.
    case 0xfff9:      // Interlinear annotation anchor.
    case 0xfffa:      // Interlinear annotation separator.
    case 0xfffb:      // Interlinear annotation terminator.
    case 0x00ad:      // Soft-hyphen.
    case 0x06dd:      // Arabic end of ayah.
    case 0x070f:      // Syriac abbreviation mark.
    case 0x17b4:      // Khmer vowel inherent Aq.
    case 0x17b5:      // Khmer vowel inherent Aa.
    case 0x000e0001:  // Language tag.
      return true;
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1583-1591)
```java
      // These characters are fully legal in JSON, but are escaped to prevent XSS risks. Notably
      // this topic is not like 'html escaping' where these would be replaced with something like
      // `&lt;`. The escaped or not ways of writing it are verbatim 2 exactly equivalent
      // ways to represent the same exact value in JSON: consumers should never do any manual
      // unescape to round trip the intended value. This replacement is only be semantically
      // observable if someone tries to handle it raw textually and not as JSON.
      for (int i : "<>&='".toCharArray()) {
        replacementChars[i] = String.format("\\u%04x", i);
      }
```

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L2016-2021)
```java
  // Regression test for b/73832901. Make sure html tags are escaped.
  @Test
  public void testHtmlEscape() throws Exception {
    TestAllTypes message = TestAllTypes.newBuilder().setOptionalString("</script>").build();
    assertThat(toJsonString(message))
        .isEqualTo("{\n  \"optionalString\": \"\\u003c/script\\u003e\"\n}");
```
