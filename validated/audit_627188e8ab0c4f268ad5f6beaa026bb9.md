Found a clear cross-language security-hardening gap in Protobuf's own JSON string encoder that is a strong structural analog to the ESAPI advisory (a "safety" mechanism that silently fails to provide the protection its own design intent claims, creating a false sense of security for the consuming application).

### Title
Inconsistent/Missing HTML/JavaScript-context escaping in Protobuf JSON string encoders (`upb`, C++, C#) versus Java, enabling XSS when ProtoJSON output is embedded in HTML - (File: `upb/json/encode.c`)

### Summary
Protobuf's Java `JsonFormat` printer explicitly escapes `<`, `>`, `&`, `=`, and `'` when writing string field values to ProtoJSON, with an in-code comment stating this is done specifically "to prevent XSS risks" [1](#0-0)  and this is directly asserted by regression tests [2](#0-1) . The C++ `json/internal/writer.cc` and C# `JsonFormatter.cs` implementations only escape 2 of those 5 characters (`<` and `>`) [3](#0-2) [4](#0-3) . Most severely, the `upb` JSON encoder — which backs Python's C extension, PHP, Ruby, and Lua bindings — escapes **none** of these characters at all; its `jsonenc_put_escaped_char` only escapes the mandatory JSON control characters and raw bytes `< 0x20`, passing `<`, `>`, `&`, `=`, and `'` through verbatim into the JSON string [5](#0-4) , a routine that is copy-identical in the generated `php-upb.c` and `ruby-upb.c` bundles [6](#0-5) .

### Finding Description
The ESAPI advisory's failed invariant is: a method that purports to validate/guarantee "safety" for downstream HTML/JS embedding returns a result that is not actually safe, misleading callers who trust its "isValid"/"safe" contract. The direct Protobuf analog is Protobuf's own JSON string writers, which were explicitly hardened (per their own comments and Java's tests) against XSS by escaping `<`, `>`, `&`, `=`, `'` because ProtoJSON output is a common target for direct embedding into `<script>` blocks in server-rendered HTML (`var data = {{json}};`). This is precisely the invariant Java's comment documents: "These characters are fully legal in JSON, but are escaped to prevent XSS risks" [1](#0-0) .

That invariant is violated in the other first-party implementations:
- C++ `MustEscape`/`WriteEscapedUtf8` only escapes `<` and `>`, not `&`, `=`, or `'` [7](#0-6) .
- C# `WriteString` mirrors the C++ behavior (only `<`/`>` treated as unsafe via the `CommonRepresentations` table and switch) [8](#0-7) .
- `upb`'s `jsonenc_put_escaped_char` — the encoder used natively by Python (C extension), PHP, and Ruby — has no special-casing for `<`, `>`, `&`, `=`, or `'` whatsoever; anything ≥ `0x20` that isn't one of the mandatory JSON escapes (`"`, `\`, control chars) is written through raw [5](#0-4) .

The attacker-controlled value is any string field content in a trusted schema (e.g., a user-supplied display name or comment field) parsed via a supported public API (binary Parse or ProtoJSON Parse) and then re-serialized to ProtoJSON via `JsonFormat`/`MessageToJson`/`upb_JsonEncode` for use by the consuming application (a common and documented usage pattern, per the very existence of this escaping code and its tests). The missing check is the absence of the XSS-oriented escape set in `upb`, C++, and C#, despite Java demonstrating this is a known, intentional, and tested security control for this exact API surface.

### Impact Explanation
Consuming applications that treat ProtoJSON output from `MessageToJson`/`json_format.MessageToJson`-equivalent bindings (Python, PHP, Ruby, Lua via upb; or C++/C#) as safe for direct embedding in an HTML `<script>` context — the exact threat model Java's own comment and tests describe — remain exposed to reflected/stored XSS if a string field contains `</script>`, `<img onerror=...>`, or similar payloads, because those bindings do not escape `<`/`>` (upb) or `&`/`=`/`'` (upb, C++, C#). This is a real gap between documented intent (present in Java) and actual behavior (absent/partial elsewhere), producing the same "false sense of safety" failure mode as the ESAPI `isValidSafeHTML` advisory: multiple language implementations of the identical library function silently diverge from the security contract implied by their own code comments and Java's regression tests.

### Likelihood Explanation
Likelihood is High for exploitation once the precondition (ProtoJSON output embedded raw into HTML/JS without independent escaping) holds — which is a plausible and officially anticipated integration pattern, since Protobuf itself added this exact escaping in Java specifically to mitigate it. Any attacker who controls a string field value processed through Python/PHP/Ruby/Lua (upb) or C++/C# ProtoJSON serialization can inject `<`, `>`, `&`, `=`, `'` unescaped into the JSON output with no additional bypass required — there is no check to defeat, since the check never existed in those bindings.

### Recommendation
Align all first-party JSON encoders (`upb/json/encode.c`, `src/google/protobuf/json/internal/writer.cc`, `csharp/src/Google.Protobuf/JsonFormatter.cs`) with the Java `JsonFormat` behavior, escaping `<`, `>`, `&`, `=`, and `'` (and ideally the same non-ASCII "Zero width"/bidi/interlinear ranges Java/C++/C# already partially cover) uniformly, then add cross-language conformance tests (mirroring `testHtmlEscapeAllGsonCharacters`) to prevent future regressions/divergence.

### Proof of Concept
Using upb-backed Python bindings, encode a message with a string field set to `"<script>alert(1)</script>"` via `MessageToJson`. The Java implementation of the identical operation on the identical proto produces `\u003cscript\u003ealert(1)\u003c/script\u003e` per `testHtmlEscape` [9](#0-8) , whereas tracing `jsonenc_string` → `jsonenc_stringbody` → `jsonenc_put_escaped_char` in `upb/json/encode.c` shows every byte of `<script>alert(1)</script>` falls into the `default` branch (none are `\n`,`\r`,`\t`,`"`,`\f`,`\b`,`\\`, or `< 0x20`), so each byte is written through unescaped via `jsonenc_putbytes` [10](#0-9) , yielding literal `"<script>alert(1)</script>"` in the JSON output — a direct XSS payload if that JSON is embedded into an HTML/script context, exactly the scenario Protobuf's own Java hardening and tests were written to prevent.

### Citations

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

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L2016-2035)
```java
  // Regression test for b/73832901. Make sure html tags are escaped.
  @Test
  public void testHtmlEscape() throws Exception {
    TestAllTypes message = TestAllTypes.newBuilder().setOptionalString("</script>").build();
    assertThat(toJsonString(message))
        .isEqualTo("{\n  \"optionalString\": \"\\u003c/script\\u003e\"\n}");

    TestAllTypes.Builder builder = TestAllTypes.newBuilder();
    JsonFormat.parser().merge(toJsonString(message), builder);
    assertThat(builder.getOptionalString()).isEqualTo(message.getOptionalString());

    // Explicitly test individual HTML unsafe characters to kill any negation mutants
    TestAllTypes message2 = TestAllTypes.newBuilder().setOptionalString("\n<>&='").build();
    assertThat(toJsonString(message2))
        .isEqualTo("{\n  \"optionalString\": \"\\n\\u003c\\u003e\\u0026\\u003d\\u0027\"\n}");

    TestAllTypes.Builder builder2 = TestAllTypes.newBuilder();
    JsonFormat.parser().merge(toJsonString(message2), builder2);
    assertThat(builder2.getOptionalString()).isEqualTo(message2.getOptionalString());
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

**File:** src/google/protobuf/json/internal/writer.cc (L284-296)
```text
void JsonWriter::WriteEscapedUtf8(absl::string_view str) {
  while (!str.empty()) {
    static constexpr absl::CharSet kUnsafeChars =
        ~absl::CharSet::AsciiPrintable() | absl::CharSet("\"\\<>");

    auto [safe_prefix, rest] = SplitAtFirst(str, kUnsafeChars);
    if (!safe_prefix.empty()) {
      Write(safe_prefix);
      str = rest;
      if (str.empty()) {
        break;
      }
    }
```

**File:** csharp/src/Google.Protobuf/JsonFormatter.cs (L61-76)
```csharp
    private static readonly string[] CommonRepresentations = {
      // C0 (ASCII and derivatives) control characters
      "\\u0000", "\\u0001", "\\u0002", "\\u0003",  // 0x00
      "\\u0004", "\\u0005", "\\u0006", "\\u0007", "\\b", "\\t", "\\n", "\\u000b", "\\f", "\\r",
      "\\u000e", "\\u000f", "\\u0010", "\\u0011", "\\u0012", "\\u0013",  // 0x10
      "\\u0014", "\\u0015", "\\u0016", "\\u0017", "\\u0018", "\\u0019", "\\u001a", "\\u001b",
      "\\u001c", "\\u001d", "\\u001e", "\\u001f",
      // Escaping of " and \ are required by www.json.org string definition.
      // Escaping of < and > are required for HTML security.
      "", "", "\\\"", "", "", "", "", "",                                            // 0x20
      "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",                // 0x30
      "", "", "", "", "\\u003c", "", "\\u003e", "", "", "", "", "", "", "", "", "",  // 0x40
      "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",                // 0x50
      "", "", "", "", "\\\\", "", "", "", "", "", "", "", "", "", "", "",            // 0x60
      "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",                // 0x70
      "", "", "", "", "", "", "", "\\u007f",
```

**File:** csharp/src/Google.Protobuf/JsonFormatter.cs (L717-731)
```csharp
        switch ((uint)c) {
          // These are not required by json spec
          // but used to prevent security bugs in javascript.
          case 0xfeff:  // Zero width no-break space
          case 0xfff9:  // Interlinear annotation anchor
          case 0xfffa:  // Interlinear annotation separator
          case 0xfffb:  // Interlinear annotation terminator

          case 0x00ad:  // Soft-hyphen
          case 0x06dd:  // Arabic end of ayah
          case 0x070f:  // Syriac abbreviation mark
          case 0x17b4:  // Khmer vowel inherent Aq
          case 0x17b5:  // Khmer vowel inherent Aa
            HexEncodeUtf16CodeUnit(writer, c);
            break;
```

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

**File:** ruby/ext/google/protobuf_c/ruby-upb.c (L5560-5593)
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
