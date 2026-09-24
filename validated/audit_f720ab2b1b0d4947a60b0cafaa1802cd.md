### Title
Python ProtoJSON printer omits HTML/XSS-sensitive character escaping (`<`, `>`, `&`, `=`, `'`) applied by the C++, Java, and C# ProtoJSON printers - (File: `python/google/protobuf/json_format.py`)

### Summary
The CKEditor4 advisory's root cause is that untrusted string content was serialized into a browser-consumed output format without escaping characters that a downstream HTML/JS context would interpret as markup, letting attacker-controlled string data cross a trust boundary into script execution. Protobuf has an internal analog of this exact invariant: the canonical ProtoJSON printers deliberately over-escape `<`, `>`, `&`, `=`, `'` (beyond what the JSON RFC requires) specifically "to prevent security bugs in javascript" when JSON output is naively embedded into an HTML/`<script>` context by a consuming application. This mitigation is implemented consistently in C++ (`src/google/protobuf/json/internal/writer.cc`), Java (`java/util/src/main/java/com/google/protobuf/util/JsonFormat.java`), and C# (`csharp/src/Google.Protobuf/JsonFormatter.cs`), but the Python `json_format.py` printer's own escaping test only exercises the JSON-spec-required escapes and does **not** demonstrate the same `<`/`>`/`&` HTML-defensive escaping.

### Finding Description
In the C++ implementation, `MustEscape` in `src/google/protobuf/json/internal/writer.cc` explicitly escapes `<` and `>` (and several Unicode confusables) with the comment "These are not required by the JSON spec, but help to prevent security bugs in JavaScript" [1](#0-0) , verified by `JsonTest.HtmlEscape` in `src/google/protobuf/json/json_test.cc` which asserts `</script>` becomes `\u003c/script\u003e` [2](#0-1) .

The Java `JsonFormat.printSingleFieldValue`'s `printStringEscapedAndQuoted` helper replicates this, explicitly escaping `<>&='` "to prevent XSS risks" [3](#0-2) , confirmed by `testHtmlEscape` and `testHtmlEscapeAllGsonCharacters` in `JsonFormatTest.java` [4](#0-3) [5](#0-4) .

C#'s `JsonFormatter.WriteString` mirrors the same `CommonRepresentations` table that maps `<` to `\u003c` and `>` to `\u003e` with the comment "Escaping of < and > are required for HTML security" [6](#0-5) [7](#0-6) .

By contrast, `python/google/protobuf/internal/json_format_test.py`'s `testJsonEscapeString` feeds the string `'&\n<"\r>\b\t\f\\\001/'` through `json_format.MessageToJson` and asserts the output is `'"&\\n<\\"\\r>\\b\\t\\f\\\\\\u0001/\\u2028\\u2029"'` [8](#0-7) . In that expected output, `&`, `<`, and `>` all appear **literally unescaped** — only the JSON-spec-mandated characters (`\n`, `\"`, `\r`, `\b`, `\t`, `\f`, `\\`, control chars, and the paragraph separators `\u2028`/`\u2029`) are escaped. This is the opposite of the C++/Java/C# behavior and of the explicit invariant those implementations enforce ("Escaping of < and > are required for HTML security").

I was not able to fully trace the exact string-serialization call path inside `python/google/protobuf/json_format.py` in this session (only the module header/imports were retrieved before the tool budget ran out), so I cannot state with certainty whether Python relies on the stdlib `json` module's `json.dumps` (which does not escape `<`/`>`/`&` by default) for message-to-JSON string values, or whether escaping occurs elsewhere (e.g., via the upb/C++ backend when the C extension is active). This is the key open question.

### Impact Explanation
If the underlying behavior is confirmed (i.e., Python's pure-Python ProtoJSON printer truly emits literal `<`, `>`, `&` in string field values), this is a stored/reflected XSS-enabling primitive class: any consuming application that (a) uses Python protobuf's `MessageToJson` to serialize a message containing attacker-controlled string fields, and (b) embeds the resulting JSON text verbatim inside an HTML `<script>` block or similar HTML context (a widely-documented anti-pattern that the C++/Java/C# escaping tables exist specifically to mitigate), would be exposed to script injection via a payload like `</script><script>alert(1)</script>` in a string field — exactly analogous to the CKEditor Fake Objects HTML injection bug, where malformed generated markup was not neutralized before being interpreted by the browser. Impact would be High (client-side script execution) if downstream apps rely on the documented safety guarantee that other language runtimes provide, since protobuf project maintainers have treated this escaping as a security-relevant hardening (see the Java comment "to prevent XSS risks" and the C# "required for HTML security" comment), implying cross-language behavioral parity is expected.

### Likelihood Explanation
Medium-Low confidence, pending confirmation. The finding is based on a test-expectation discrepancy across three independent, security-motivated reference implementations vs. what the Python test literally asserts. It's possible that: (1) the Python C-extension (upb) backend already performs this escaping and the pure-Python path is a legacy/fallback rarely exercised, (2) the test I found is outdated/wrong, or (3) escaping happens in a code path not read in this session. Because I could not read the actual Python string-serialization implementation (e.g., `_ValueToJson`/`_RegularMessageToJsonObject` in `json_format.py`) before running out of turns, I cannot make a definitive determination of exploitability — this should be verified with direct code reading and by running the exact `testJsonEscapeString`/`testHtmlEscape`-style payload against the current Python protobuf runtime (both pure-Python and upb-backed) before treating this as confirmed.

### Recommendation
1. Read `python/google/protobuf/json_format.py`'s string-serialization code path (search for the field-to-JSON-value conversion function that handles `CPPTYPE_STRING`) to determine whether `<`, `>`, `&`, `=`, `'` are escaped, and whether behavior differs between the pure-Python and C++/upb-backed implementations.
2. If unescaped, align Python's ProtoJSON string printer with the C++/Java/C# behavior by escaping `<`, `>` (and ideally `&`, `=`, `'` for parity) using `\uXXXX` escapes, matching `MustEscape` in `writer.cc`.
3. Add/restore a `testHtmlEscape`-equivalent regression test in `json_format_test.py` asserting `</script>` round-trips to `\u003c/script\u003e`, mirroring the C++/Java tests, to prevent regression.
4. Audit other language bindings (Go, Ruby, PHP, Rust, Objective-C) for the same discrepancy, since this escaping is a security-hardening measure that should be uniformly applied everywhere `MessageToJson`-equivalent APIs are public and can be fed attacker-controlled string data.

### Proof of Concept
Not run in this session (no execution environment available). Suggested reproduction for verification:
```python
from google.protobuf import json_format
import my_proto_pb2

m = my_proto_pb2.TestMessage(string_value="</script><script>alert(1)</script>")
print(json_format.MessageToJson(m))
```
Expected (if vulnerable, matching the literal-unescaped behavior shown in the existing test at `python/google/protobuf/internal/json_format_test.py:296-304`): output contains literal `</script><script>alert(1)</script>` rather than `\u003c/script\u003e...`. Compare against the C++ (`JsonTest.HtmlEscape`, `src/google/protobuf/json/json_test.cc:1583-1589`) and Java (`testHtmlEscape`, `java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java:2016-2035`) equivalents, which are confirmed via their test assertions to escape these characters. This PoC should be executed against a live checkout to convert this from a suspected discrepancy into a confirmed vulnerability before filing.

### Citations

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

**File:** src/google/protobuf/json/json_test.cc (L1583-1589)
```text
TEST_P(JsonTest, HtmlEscape) {
  TestMessage m;
  m.set_string_value("</script>");
  EXPECT_THAT(ToJson(m),
              IsOkAndHolds(R"({"stringValue":"\u003c/script\u003e"})"));

}
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

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L2719-2738)
```java
  @Test
  public void testHtmlEscapeAllGsonCharacters() throws Exception {
    // Gson HTML-escapes these characters by default: < > & = '
    TestAllTypes message =
        TestAllTypes.newBuilder().setOptionalString("<tag>&amp='value'</tag>").build();
    String json = toJsonString(message);
    assertThat(json).contains("\\u003c"); // <
    assertThat(json).contains("\\u003e"); // >
    assertThat(json).contains("\\u0026"); // &
    assertThat(json).contains("\\u003d"); // =
    assertThat(json).contains("\\u0027"); // '
    assertThat(json).doesNotContain("<");
    assertThat(json).doesNotContain(">");
    assertThat(json).doesNotContain("&");
    assertThat(json).doesNotContain("=");

    TestAllTypes.Builder builder = TestAllTypes.newBuilder();
    JsonFormat.parser().merge(json, builder);
    assertThat(builder.getOptionalString()).isEqualTo("<tag>&amp='value'</tag>");
  }
```

**File:** csharp/src/Google.Protobuf/JsonFormatter.cs (L61-75)
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
```

**File:** csharp/src/Google.Protobuf/JsonFormatter.cs (L694-748)
```csharp
    internal static void WriteString(TextWriter writer, string text) {
      writer.Write('"');
      for (int i = 0; i < text.Length; i++) {
        char c = text[i];
        if (c < 0xa0) {
          writer.Write(CommonRepresentations[c]);
          continue;
        }
        if (char.IsHighSurrogate(c)) {
          // Encountered first part of a surrogate pair.
          // Check that we have the whole pair, and encode both parts as hex.
          i++;
          if (i == text.Length || !char.IsLowSurrogate(text[i])) {
            throw new ArgumentException(
                "String contains low surrogate not followed by high surrogate");
          }
          HexEncodeUtf16CodeUnit(writer, c);
          HexEncodeUtf16CodeUnit(writer, text[i]);
          continue;
        } else if (char.IsLowSurrogate(c)) {
          throw new ArgumentException(
              "String contains high surrogate not preceded by low surrogate");
        }
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

          default:
            if ((c >= 0x0600 && c <= 0x0603) ||  // Arabic signs
                (c >= 0x200b && c <= 0x200f) ||  // Zero width etc.
                (c >= 0x2028 && c <= 0x202e) ||  // Separators etc.
                (c >= 0x2060 && c <= 0x2064) ||  // Invisible etc.
                (c >= 0x206a && c <= 0x206f)) {
              HexEncodeUtf16CodeUnit(writer, c);
            } else {
              // No handling of surrogates here - that's done earlier
              writer.Write(c);
            }
            break;
        }
      }
      writer.Write('"');
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
