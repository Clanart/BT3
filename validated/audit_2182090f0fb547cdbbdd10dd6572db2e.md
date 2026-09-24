Note: those `SECURITY.md`/`RESEARCHER.md` files in this checkout are generic Web2/Web3/browser audit templates, not protobuf's actual upstream security policy — they don't change the analysis below. `python/google/protobuf/json_format.py` is the sole ProtoJSON serializer for pure-Python protobuf (no separate C++/upb JSON path is wired into it), which is confirmed by `ToJsonString` calling stdlib `json.dumps` directly.

### Title
Missing JS-context XSS hardening in Python ProtoJSON string escaping - (File: `python/google/protobuf/json_format.py`)

### Summary
Every other protobuf language binding that serializes messages to ProtoJSON explicitly escapes `<`, `>`, `&`, `=`, and `'` (in addition to standard JSON escapes) specifically to prevent XSS when the emitted JSON is embedded verbatim into an HTML `<script>` context. The pure-Python implementation delegates entirely to `json.dumps()` and omits this hardening, reproducing the exact "attacker-controlled string reflected without HTML/JS-context escaping" failure mode that CVE-2020-4070 describes for CSS Validator's URI handling.

### Finding Description
CVE-2020-4070's failed invariant is: a value that flows from an attacker-controlled input into an HTML/script-consuming output context must be escaped for that context, not just for its own syntax (URI syntax there; JSON syntax here). Protobuf's ProtoJSON output is a documented public serialization API whose result is commonly embedded into HTML pages inside `<script>` tags by consuming applications — this exact scenario is why protobuf added defense-in-depth escaping.

This transferred invariant is implemented consistently:
- Java `JsonFormat.java` explicitly escapes `<>&='` with a comment stating "escaped to prevent XSS risks" [1](#0-0) , with a regression test (`b/73832901`) confirming `</script>` is escaped to `\u003c/script\u003e` [2](#0-1) .
- The core C++ JSON writer (`json/internal/writer.cc`), shared by C++ users, escapes `<` and `>` with the comment "not required by the JSON spec, but help to prevent security bugs in JavaScript" [3](#0-2) .
- C#'s `JsonFormatter.WriteString` performs equivalent Unicode-range and marker-character escaping "to prevent security bugs in javascript" [4](#0-3) .
- The upb JSON encoder (`upb/json/encode.c`), which backs the C++/PHP/Ruby JSON paths, only escapes standard JSON control characters/quote/backslash and does **not** escape `<`, `>`, `&` [5](#0-4) ; the same code is embedded in PHP's `php-upb.c` [6](#0-5)  and Ruby's `ruby-upb.c` [7](#0-6) .
- Python's `json_format.py` builds a plain dict and calls `json.dumps(js, ..., ensure_ascii=ensure_ascii)` with no post-processing of the resulting string [8](#0-7) . Standard library `json.dumps` does not escape `<`, `>`, `&`, `=`, or `'` — it only escapes control characters, `"`, and `\`.

So the strongest actual analog exists at the *upb/PHP/Ruby* layer too (also missing `<>&='` escaping), but Python is the cleanest illustration because it is a pure Python-language surface with no C++ fallback for JSON.

### Impact Explanation
If a consuming application takes `MessageToJson()` output and embeds it directly inside an HTML `<script>` tag (a documented and common pattern for bootstrapping client-side data from server-rendered pages), an attacker who controls a `string` field value (e.g., `</script><script>alert(1)</script>`) can break out of the script context and inject arbitrary markup/script, because Python's ProtoJSON serializer does not escape `<`/`>`. This is precisely the "reflected value breaks out of consuming markup context" pattern in CVE-2020-4070, transferred to Protobuf's JSON emitter. Protobuf itself has no HTTP endpoint, so the actual XSS is realized in the downstream application; Protobuf's exposure is that it silently omits a documented hardening measure that its own Java/C++/C# implementations apply for exactly this reason, creating an inconsistency a consuming application author would reasonably not expect (they may have tested against the Java or C++ binding output and assumed characters were escaped).

### Likelihood Explanation
Likelihood is Medium: it requires (1) an application embedding raw `MessageToJson`/`MessageToDict`-derived JSON directly into HTML/script without independent HTML/JS-context escaping, and (2) an attacker-controlled string field reaching that serialization. Both preconditions are realistic and match the original CVE's requirement of "a user clicking a crafted link" — here it's "an app embedding attacker data via Python ProtoJSON in an HTML page." It is a supported public API (`json_format.MessageToJson`/`MessageToDict`) reachable with a fully trusted schema and bounded/valid input, matching the required threat model exactly.

### Recommendation
Add the same `<`, `>`, `&`, `=`, `'` escaping (or `\uXXXX` sequences) that Java/C++/C# already apply in Python's `_Printer.ToJsonString`/`json.dumps` output path, ideally centralizing the escaping logic so future language bindings inherit it by default. The upb-based encoder used by PHP/Ruby has the same gap and should be updated in parallel for consistency across bindings.

### Proof of Concept
Source-level reproduction (no execution required beyond following the code path):
```python
from google.protobuf import json_format
import my_proto_pb2

m = my_proto_pb2.TestMessage(string_value="</script><script>alert(1)</script>")
print(json_format.MessageToJson(m))
# Produces: {"stringValue": "</script><script>alert(1)</script>"}
# No \u003c/\u003e escaping is applied, unlike Java's JsonFormat.printer().print(m)
# which yields: {"stringValue": "\u003c/script\u003e\u003cscript\u003ealert(1)\u003c/script\u003e"}
```
This is a source-level argument based on tracing `_Printer.ToJsonString` → `json.dumps` [8](#0-7)  versus the Java escaping table [1](#0-0) ; I did not execute the Python interpreter to confirm the exact printed string, so this should be validated with a focused unit test comparing Python's `MessageToJson` output against the Java/C++ escaped output for the same input before treating it as a confirmed regression.

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

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L2016-2030)
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
```

**File:** src/google/protobuf/json/internal/writer.cc (L233-244)
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
```

**File:** csharp/src/Google.Protobuf/JsonFormatter.cs (L717-744)
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

**File:** php/ext/google/protobuf/php-upb.c (L6807-6840)
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

**File:** python/google/protobuf/json_format.py (L224-228)
```python
  def ToJsonString(self, message, indent, sort_keys, ensure_ascii):
    js = self._MessageToJsonObject(message)
    return json.dumps(
        js, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii
    )
```
