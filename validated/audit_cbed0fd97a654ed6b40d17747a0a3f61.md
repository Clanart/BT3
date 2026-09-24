Confirmed: `TextFormatEscaper.escapeDoubleQuotesAndBackslashes` only escapes `\` and `"` [1](#0-0) , and the caller only additionally normalizes `\n` (`replace("\n", "\\n")`), leaving `\r`, `\t`, backspace, form-feed, vertical-tab, bell, and ESC (0x1b) unescaped when `escapeNonAscii` is disabled [2](#0-1) .

### Title
Incomplete control-character escaping in `TextFormat` Unicode-mode string printing allows terminal/ANSI injection - (File: `java/core/src/main/java/com/google/protobuf/TextFormat.java`)

### Summary
When `TextFormat.Printer` is configured with `escapingNonAscii(false)` (or via the deprecated `TextFormat.printUnicode()` / `printToUnicodeString()` APIs), `printFieldValue()` for `STRING` fields routes through `escapeDoubleQuotesAndBackslashes(...).replace("\n", "\\n")` instead of the full `TextFormatEscaper.escapeText()` used in the default path [2](#0-1) . This partial escaper neutralizes only `\`, `"`, and `\n`, but passes through all other C0 control characters — including `\r` (carriage return) and ESC (0x1b, the ANSI escape introducer) — verbatim into the emitted text [1](#0-0) . This mirrors the failed invariant in CVE-2019-6109: a display/serialization routine that is expected to neutralize control bytes before they reach a terminal-consuming sink fails to filter all of them, permitting cursor movement / line-clearing / hidden-text tricks via ANSI escape sequences.

### Finding Description
- **Attacker-controlled value**: An ordinary client can set an arbitrary UTF-8/Latin-1 `string` field value (e.g. containing `\x1b[2K\r`, ANSI SGR sequences, or other C0 controls) through the standard binary-Protobuf or ProtoJSON parse APIs. Schemas/generated code are trusted; only the field content is attacker-controlled.
- **Consuming-application exposure assumption**: Any application that calls `message.toString()`-equivalent Unicode-mode text output (`TextFormat.printer().escapingNonAscii(false)`, or the deprecated `TextFormat.printUnicode`/`printToUnicodeString`) and writes the result to a terminal, log viewer, or terminal-emulating console (a very common debugging/logging pattern) is exposed. This is the direct analog of OpenSSH's `refresh_progress_meter()` writing server/attacker-supplied filenames straight to the terminal.
- **Missing check**: The default (`escapeNonAscii = true`) path goes through `TextFormatEscaper.escapeText()`, which has a complete `REPLACEMENT_CHARS` table covering all bytes `0x00`–`0x1f` and `0x7f` [3](#0-2) . The Unicode-mode fallback deliberately bypasses this table and only performs two `String.replace` calls plus a manual `\n` substitution, so bytes such as `\r`, `\t`, `\b`, `\f`, `0x0b`, `0x07`, and critically ESC (`0x1b`) are emitted unescaped [2](#0-1) .
- **Contrast with hardened paths**: The C++ `TextFormat::Printer::HardenedPrintString` / `FastFieldValuePrinter::PrintString` [4](#0-3) , upb's `_upb_HardenedPrintString` [5](#0-4) , and Java's `JsonFormat` printer (which explicitly escapes all C0 controls plus `<>&='` for XSS/terminal-safety reasons) [6](#0-5)  all escape the full control-character range. Only the Java TextFormat "Unicode" compatibility mode deviates from this invariant.

### Impact Explanation
If application code logs or prints TextFormat output produced in Unicode mode to a terminal, an attacker who controls a string field (e.g., a request/message payload later echoed via `TextFormat.printUnicode`) can inject ANSI escape sequences to move the cursor, overwrite/hide previously printed lines, spoof prompts, or hide the presence of other injected content in operator-facing terminal output — the same class of terminal-spoofing impact as CVE-2019-6109. It does not achieve memory corruption or code execution; the impact is confined to terminal-output integrity/spoofing in operator/security tooling.

### Likelihood Explanation
Reachable whenever an application intentionally opts into `escapingNonAscii(false)` (or uses the deprecated `printUnicode`/`printToUnicodeString` convenience methods) and pipes the output to a terminal — a realistic but non-default configuration, since the default `Printer` (`escapeNonAscii = true`) is fully hardened. Likelihood is Medium: it requires the consuming application to choose the Unicode-mode printer and to display the result on a terminal, but no other precondition (e.g., malicious peer, privileged access) is needed beyond an ordinary untrusted string field.

### Recommendation
Route the `escapeNonAscii = false` branch of `printFieldValue()` through a control-character-safe escaper (e.g., reuse `TextFormatEscaper`'s replacement table for bytes `< 0x20` and `0x7f`, while still leaving bytes `>= 0x80` unescaped for the "Unicode" behavior), rather than only handling `\`, `"`, and `\n`. This preserves the intended "don't octal-escape non-ASCII" behavior of Unicode mode while closing the terminal-injection gap for all C0 control bytes.

### Proof of Concept
Based on the code paths above, the following demonstrates the gap (not executed in this environment — recommend a Devin session to run it against the checkout):
```java
TestAllTypes msg = TestAllTypes.newBuilder()
    .setOptionalString("safe\u001b[31mFAKE ERROR\u001b[0m\rhidden")
    .build();
String out = TextFormat.printer().escapingNonAscii(false)
    .printToString(msg);
// out contains raw ESC (0x1b) and \r bytes unescaped, e.g.:
// optional_string: "safe\u001b[31mFAKE ERROR\u001b[0m\rhidden"
// where \u001b and \r are literal, unescaped bytes when written to a terminal,
// unlike the default printer() which fully octal-escapes them via TextFormatEscaper.escapeText().
```
This is consistent with existing unit tests confirming that only `\n` (not `\r` or ESC) is special-cased in Unicode mode [7](#0-6) .

### Citations

**File:** java/core/src/main/java/com/google/protobuf/TextFormatEscaper.java (L93-111)
```java
  private static final String[] REPLACEMENT_CHARS;

  static {
    REPLACEMENT_CHARS = new String[128];
    for (int i = 0; i <= 0x1f; i++) {
      REPLACEMENT_CHARS[i] = String.format("\\%03o", i);
    }
    REPLACEMENT_CHARS[0x7f] = "\\177";
    REPLACEMENT_CHARS['\"'] = "\\\"";
    REPLACEMENT_CHARS['\''] = "\\\'";
    REPLACEMENT_CHARS['\\'] = "\\\\";
    REPLACEMENT_CHARS['\t'] = "\\t";
    REPLACEMENT_CHARS['\b'] = "\\b";
    REPLACEMENT_CHARS['\n'] = "\\n";
    REPLACEMENT_CHARS['\r'] = "\\r";
    REPLACEMENT_CHARS['\f'] = "\\f";
    REPLACEMENT_CHARS[0x07] = "\\a";
    REPLACEMENT_CHARS[0x0b] = "\\v";
  }
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormatEscaper.java (L151-154)
```java
  /** Escape double quotes and backslashes in a String for unicode output of a message. */
  static String escapeDoubleQuotesAndBackslashes(String input) {
    return input.replace("\\", "\\\\").replace("\"", "\\\"");
  }
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L633-640)
```java
        case STRING:
          generator.print("\"");
          generator.print(
              escapeNonAscii
                  ? TextFormatEscaper.escapeText((String) value)
                  : escapeDoubleQuotesAndBackslashes((String) value).replace("\n", "\\n"));
          generator.print("\"");
          break;
```

**File:** src/google/protobuf/text_format.cc (L2214-2238)
```text
namespace {

bool ContainsCharactersToCEscape(absl::string_view val) {
  bool needs_escape = false;
  for (unsigned char c : val) {
    needs_escape |=
        ((c < 32) | (c > 126) | (c == '"') | (c == '\'') | (c == '\\'));
  }
  return needs_escape;
}

}  // namespace

void TextFormat::FastFieldValuePrinter::PrintString(
    const std::string& val, BaseTextGenerator* generator) const {
  generator->PrintLiteral("\"");
  if (!val.empty()) {
    if (ABSL_PREDICT_FALSE(ContainsCharactersToCEscape(val))) {
      generator->PrintString(absl::CEscape(val));
    } else {
      generator->PrintString(val);
    }
  }
  generator->PrintLiteral("\"");
}
```

**File:** upb/text/internal/encode.h (L178-202)
```text
UPB_INLINE void UPB_PRIVATE(_upb_HardenedPrintString)(txtenc* e,
                                                      const char* ptr,
                                                      size_t len) {
  // Print as UTF-8, while guarding against any invalid UTF-8 in the string
  // field.
  //
  // If in the future we have a guaranteed invariant that invalid UTF-8 will
  // never be present, we could avoid the UTF-8 check here.
  UPB_PRIVATE(_upb_TextEncode_PutStr)(e, "\"");
  const char* end = ptr + len;
  while (ptr < end) {
    size_t n = UPB_PRIVATE(_SkipPassthroughBytes)(ptr, end - ptr);
    if (n != 0) {
      UPB_PRIVATE(_upb_TextEncode_PutBytes)(e, ptr, n);
      ptr += n;
      if (ptr == end) break;
    }

    // If repeated calls to CEscape() and PrintString() are expensive, we could
    // consider batching them, at the cost of some complexity.
    UPB_PRIVATE(_upb_TextEncode_Escaped)(e, *ptr);
    ptr++;
  }
  UPB_PRIVATE(_upb_TextEncode_PutStr)(e, "\"");
}
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1568-1592)
```java
    private static final String[] replacementChars;

    static {
      replacementChars = new String[128];
      for (int i = 0; i <= 0x1f; i++) {
        replacementChars[i] = String.format("\\u%04x", i);
      }
      replacementChars['"'] = "\\\"";
      replacementChars['\\'] = "\\\\";
      replacementChars['\t'] = "\\t";
      replacementChars['\b'] = "\\b";
      replacementChars['\n'] = "\\n";
      replacementChars['\r'] = "\\r";
      replacementChars['\f'] = "\\f";

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

**File:** java/core/src/test/java/com/google/protobuf/TextFormatTest.java (L1442-1462)
```java
  @Test
  public void testPrintToUnicodeStringWithNewlines() throws Exception {
    // No newlines at start and end
    assertThat(
            TextFormat.printer()
                .escapingNonAscii(false)
                .printToString(
                    TestAllTypes.newBuilder()
                        .setOptionalString("test newlines\n\nin\nstring")
                        .build()))
        .isEqualTo("optional_string: \"test newlines\\n\\nin\\nstring\"\n");

    // Newlines at start and end
    assertThat(
            TextFormat.printer()
                .escapingNonAscii(false)
                .printToString(
                    TestAllTypes.newBuilder()
                        .setOptionalString("\ntest\nnewlines\n\nin\nstring\n")
                        .build()))
        .isEqualTo("optional_string: \"\\ntest\\nnewlines\\n\\nin\\nstring\\n\"\n");
```
