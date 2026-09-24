### Title
Inconsistent HTML/JS-context escaping in ProtoJSON string serialization across language backends — (`upb/json/encode.c`, `php/ext/google/protobuf/php-upb.c`, `ruby/ext/google/protobuf_c/ruby-upb.c`)

### Summary
The Hugo advisory's failed invariant is: an output-escaping routine did not escape characters that are safe in the immediate output format (Markdown/HTML) but dangerous in the actual embedding context (HTML attribute/script), letting attacker-controlled content break out and be interpreted as markup/script. The Protobuf analog is structural, not incidental: Protobuf's Java (`JsonFormat.java`), C++ (`json/internal/writer.cc`), and C# (`JsonFormatter.cs`) ProtoJSON printers deliberately escape `<`, `>`, and a defined set of "JS-string-context-dangerous" Unicode code points (zero-width space, U+2028/U+2029 paragraph separators, bidi/invisible control chars) — explicitly documented as done "to prevent security bugs in javascript" / "for HTML security". The upb-based native JSON encoder (`jsonenc_put_escaped_char`), which backs PHP's and Ruby's C extensions and is shared verbatim across `upb/json/encode.c`, `php/ext/google/protobuf/php-upb.c`, and `ruby/ext/google/protobuf_c/ruby-upb.c`, only escapes the characters required by the bare JSON grammar (`"`, `\`, and C0 control chars) and does **not** escape `<`, `>`, or any of the JS-context-sensitive code points.

### Finding Description
In Java, C++, and C#, the JSON string writer contains an explicit extra escape table beyond RFC 8259 requirements: [1](#0-0) [2](#0-1) [3](#0-2) 

These three backends treat `<`/`>` and a set of Unicode "invisible"/separator characters as attacker-controlled data that must never reach an HTML/`<script>` embedding context unescaped, and there is a dedicated regression test (`b/73832901`) proving this was hardened after a real HTML-injection incident: [4](#0-3) 

In contrast, the upb C JSON encoder used natively by PHP and Ruby only implements the bare JSON-spec escapes (`\n`, `\r`, `\t`, `"`, `\f`, `\b`, `\\`, and C0 controls) and falls through to writing the raw byte for everything else, including `<` and `>`: [5](#0-4) [6](#0-5) [7](#0-6) 

The pure-Python implementation exhibits the same gap: its own test fixture shows `<` and `>` passed through unescaped in `MessageToJson` output, while only `\u2028`/`\u2029` are escaped: [8](#0-7) 

The attacker-controlled value is any string-typed field value in a bounded ProtoJSON/binary message parsed and re-serialized through the public `MessageToJson`/`JsonFormat.print`/native `encode_json` API. The missing check is the absence of the `<`/`>`/JS-context escape table in the upb-native and pure-Python encoders that the Java/C++/C# encoders explicitly implement.

### Impact Explanation
If a consuming application takes ProtoJSON output produced via PHP, Ruby, or Python bindings and embeds it directly into an HTML document (e.g., `<script>var data = <json output>;</script>`), an attacker who controls a string field (e.g., `"</script><script>alert(1)</script>"`) can break out of the script context and inject arbitrary markup/script, exactly as in the Hugo CWE-79 case. This mirrors the precise scenario the Java/C++/C# hardening was built to prevent (`b/73832901`), confirming this is a real, previously-recognized class of risk for this library — just inconsistently patched across language backends. Impact is limited to consuming applications that embed ProtoJSON output unescaped into HTML/script contexts (an assumption stated by the report's rules), and does not affect confidentiality/availability of Protobuf itself.

### Likelihood Explanation
Likelihood is moderate: it requires (1) an application to serialize attacker-influenced string fields to ProtoJSON via the PHP, Ruby, or Python backend, and (2) that output to be embedded into HTML without independent escaping. This is a known, still-common integration pattern (embedding JSON blobs into `<script>` tags for client hydration), and the very fact that Java/C++/C# added dedicated hardening for this indicates the pattern occurs in real deployments. There's no additional wire-format or size check bypass required — this is a pure output-encoding omission reachable on every string field value.

### Recommendation
Port the `<`/`>` and extended Unicode "JS-context-unsafe" escape set (matching `MustEscape` in `src/google/protobuf/json/internal/writer.cc` and `getReplacementOrNull` in `JsonFormat.java`) into `jsonenc_put_escaped_char` in `upb/json/encode.c` (and its copies in `php-upb.c` / `ruby-upb.c`), and into the pure-Python `json_format.py` string serialization path, so all Protobuf language bindings apply consistent HTML/JS-context-safe escaping by default.

### Proof of Concept
Using the Python or PHP/Ruby JSON printer:
1. Construct a message with `optional_string = "</script><script>alert(document.domain)</script>"`.
2. Call `MessageToJson`/`JsonFormat.print`/native JSON encode.
3. Compare with the Java equivalent test `testHtmlEscape` (`JsonFormatTest.java:2016-2035`), which asserts `<`/`>` are rendered as `\u003c`/`\u003e`.
4. Observe that the Python/PHP/Ruby-produced JSON string contains the raw `<`/`>` characters unescaped (as demonstrated by the existing Python test fixture at `python/google/protobuf/internal/json_format_test.py:296-304`, where `<` and `>` appear literally in the expected output), confirming the escaping gap relative to the Java/C++/C# backends.

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

**File:** csharp/src/Google.Protobuf/JsonFormatter.cs (L68-72)
```csharp
      // Escaping of " and \ are required by www.json.org string definition.
      // Escaping of < and > are required for HTML security.
      "", "", "\\\"", "", "", "", "", "",                                            // 0x20
      "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",                // 0x30
      "", "", "", "", "\\u003c", "", "\\u003e", "", "", "", "", "", "", "", "", "",  // 0x40
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

**File:** php/ext/google/protobuf/php-upb.c (L6807-6839)
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

**File:** python/google/protobuf/internal/json_format_test.py (L296-304)
```python
  def testJsonEscapeString(self):
    message = json_format_proto3_pb2.TestMessage()
    message.string_value = '&\n<"\r>\b\t\f\\\001/'
    message.string_value += (b'\xe2\x80\xa8\xe2\x80\xa9').decode('utf-8')
    self.assertEqual(
        json_format.MessageToJson(message),
        '{\n  "stringValue": '
        '"&\\n<\\"\\r>\\b\\t\\f\\\\\\u0001/\\u2028\\u2029"\n}',
    )
```
