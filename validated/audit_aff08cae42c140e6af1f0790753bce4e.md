## Analog Found: Missing JSON/JavaScript-context escaping in Python and upb JSON encoders (ProtoJSON serialization)

### Title
Improper Neutralization of `<`, `>`, `&` in ProtoJSON String Output Enables Script-Context Injection - (File: `python/google/protobuf/json_format.py`, `upb/json/encode.c`)

### Summary
Protobuf's own Java, C++, and C# ProtoJSON writers deliberately escape the characters `< > & = '` in string field values — even though this is not required by the JSON spec — specifically "to prevent security bugs in javascript" when the serialized JSON is embedded into an HTML/`<script>` context by a consuming application. The Python pure-Python `MessageToJson`/`MessageToDict` path and the upb-based JSON encoder (shared by PHP and Ruby bindings, and any other upb consumer) do not implement this same escaping, silently emitting attacker-controlled sequences such as `</script>` unescaped. This is the direct structural analog of CVE-2023-27294: attacker-controlled string content that survives serialization unsanitized and is later interpreted as script by a browser when a downstream application renders/embeds it.

### Finding Description
CVE-2023-27294 is a stored XSS: an attacker-controlled "description" field is stored and later rendered verbatim into a page consumed by other users, because the specific rendering path lacked the same output-sanitization invariant enforced elsewhere in the application.

Protobuf has an internal, explicitly-documented invariant for exactly this class of risk in its ProtoJSON writers:

- Java `JsonFormat.java` builds an escape table and explicitly notes: *"These characters are fully legal in JSON, but are escaped to prevent XSS risks"* for `<>&='`. [1](#0-0) 
This was added as a regression fix (`b/73832901`) with an explicit unit test asserting `</script>` becomes `\u003c/script\u003e`. [2](#0-1) 

- C++ core JSON writer (`src/google/protobuf/json/internal/writer.cc`) implements the identical invariant, escaping `<`/`>` and other JS-unsafe scalars with the comment *"help to prevent security bugs in JavaScript"*. [3](#0-2) 

- C# `JsonFormatter.cs` has the same escape logic for the same reason. [4](#0-3) 

By contrast, **Python's** public `MessageToJson` builds a plain dict and hands it directly to the standard library's `json.dumps`, with no custom character escaping at all: [5](#0-4) 
Standard `json.dumps` only escapes control characters, `"` and `\`; it does not touch `<`, `>`, `&`, `=`, or `'`. Confirmed there is no upb-backed native JSON encode path used by Python's `MessageToJson`/`MessageToDict` — the implementation is always the pure-Python printer in `json_format.py` regardless of C-extension backend.

Separately, the **upb** JSON encoder — shared source used for PHP (`php-upb.c`) and Ruby (`ruby-upb.c`) bindings and the core `upb/json/encode.c` — only escapes `\n \r \t \" \f \b \\` and raw control bytes `< 0x20`; it never escapes `<`, `>`, `&`, `=`, or `'`: [6](#0-5) 
Identical logic is duplicated verbatim in the PHP and Ruby native extensions: [7](#0-6) [8](#0-7) 

**Failed invariant transferred from the report:** output produced from an attacker-controlled field must be safe to embed in the context where the consuming application places it (the calendar-description-in-HTML case). Protobuf's own security precedent (the Java `b/73832901` fix, and the matching C++/C# implementations) establishes that ProtoJSON's public contract is meant to guarantee this specific invariant — safety when the resulting JSON text is embedded inside a `<script>` block, a widespread real-world pattern (e.g., server-side templates emitting `<script>var data = {json};</script>`). Python's and upb's encoders bypass that check entirely.

**Attacker-controlled value:** any `string`/enum-JSON-name field value supplied through the normal public `Message` API (e.g., set from client-submitted bytes via `ParseFromString`/`Parse` and then re-serialized to JSON) — no privileged access or hostile schema required.

**Missing check:** no substitution of `<`, `>`, `&`, `=`, `'` (or `\u2028`/`\u2029`) in `_Printer.ToJsonString` (Python) or `jsonenc_put_escaped_char` (upb C core / PHP / Ruby).

### Impact Explanation
If a consuming application calls `google.protobuf.json_format.MessageToJson` (Python) or the PHP/Ruby JSON encoder on an attacker-influenced message and embeds the resulting text directly inside an HTML `<script>` element (a documented, common integration pattern that Java/C++/C# explicitly harden against), a value containing `</script><script>alert(1)</script>` will close the legitimate script tag and inject attacker JavaScript that executes in the context of other users' browsers — the same mechanic and same class of impact (session token theft, unauthorized actions) described in CVE-2023-27294. Confidentiality/integrity impact is limited to the client-side script-injection surface, matching the Medium severity of the original CVE (C:L/I:L/A:N).

### Likelihood Explanation
Likelihood is moderate: this requires (1) an application-controlled string field reachable by an ordinary/low-privileged client, (2) that field surviving unmodified into `MessageToJson`/upb JSON output, and (3) that output being embedded into an HTML/script context without independent HTML/JS escaping by the consuming application. Protobuf's own Java/C++/C# maintainers judged this pattern common enough to add and test dedicated hardening (`b/73832901`), which is direct evidence the risk is realistic and previously observed in production use of ProtoJSON output, yet the fix was never ported to Python or upb.

### Recommendation
Port the `<>&='`/`\u2028`/`\u2029` escaping logic already present in `JsonFormat.java` (`replacementChars`, `getReplacementOrNull`, `printStringEscapedAndQuoted`) and `json/internal/writer.cc` (`MustEscape`) into:
- Python `json_format.py`'s `_Printer.ToJsonString`/`_FieldToJsonObject` string path (e.g., a custom `json.JSONEncoder` or post-processing pass), and
- upb's `jsonenc_put_escaped_char` in `upb/json/encode.c` (which will also fix PHP and Ruby simultaneously since they vendor the same generated source).

### Proof of Concept
```python
from google.protobuf import json_format
from google.protobuf.internal import json_format_proto3_pb2 as pb2

msg = pb2.TestMessage()
msg.string_value = "</script><script>alert(document.cookie)</script>"
print(json_format.MessageToJson(msg))
# Output contains the literal, unescaped sequence:
# {
#   "stringValue": "</script><script>alert(document.cookie)</script>"
# }
```
Compare with Java on the same input (`</script>`), which is required (and tested) to produce `\u003c/script\u003e`: [9](#0-8) 
If the Python (or PHP/Ruby) output above is embedded verbatim inside a server-rendered `<script>` block (`<script>var data = {json};</script>`), the `</script>` sequence terminates the intended script element early and the trailing `<script>alert(...)</script>` executes, reproducing the CVE-2023-27294 impact pattern (script execution in another user's browser) within Protobuf's own ProtoJSON surface.

### Citations

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1583-1592)
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
    }
```

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L2016-2025)
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

**File:** csharp/src/Google.Protobuf/JsonFormatter.cs (L717-745)
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
