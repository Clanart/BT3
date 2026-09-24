### Title
TextFormat `Printer.escapingNonAscii(false)` leaves raw carriage-return (`\r`) unescaped in string field output, enabling text-format field/line injection - ([File: java/core/src/main/java/com/google/protobuf/TextFormat.java])

### Summary
When a Java `TextFormat.Printer` is configured with `escapingNonAscii(false)`, string field values are escaped with `TextFormatEscaper.escapeDoubleQuotesAndBackslashes()` followed by a manual `.replace("\n", "\\n")`, but no equivalent replacement is performed for `\r` (carriage return). Every other TextFormat/JSON string encoder in this codebase (`TextFormatEscaper.escapeText`, C++ `FastFieldValuePrinter::PrintString`/`HardenedPrintString`, upb `_upb_TextEncode_Escaped`, ObjC `AppendStringEscaped`, Python `text_encoding.CEscape`, JSON `printStringEscapedAndQuoted`) explicitly escapes both `\n` and `\r`. This one code path breaks that invariant.

### Finding Description
The vulnerable code is in `TextFormat.Printer.printFieldValue`:

```java
case STRING:
  generator.print("\"");
  generator.print(
      escapeNonAscii
          ? TextFormatEscaper.escapeText((String) value)
          : escapeDoubleQuotesAndBackslashes((String) value).replace("\n", "\\n"));
  generator.print("\"");
  break;
``` [1](#0-0) 

`escapeDoubleQuotesAndBackslashes` only escapes `\` and `"`:
```java
static String escapeDoubleQuotesAndBackslashes(String input) {
  return input.replace("\\", "\\\\").replace("\"", "\\\"");
}
``` [2](#0-1) 

The subsequent `.replace("\n", "\\n")` escapes linefeed, but a literal carriage return (`\r`) in an attacker-controlled string field is emitted verbatim inside the quoted TextFormat string. This matches the Netty root cause exactly: a control character (`\r`/`\n`) that delimits protocol/format structure is not neutralized before being concatenated into a structured text output that a downstream parser will consume as multiple logical lines/records.

**Failed invariant (Netty):** SMTP command boundaries (`\r\n`) inside a user-controlled parameter must be stripped/rejected before being placed into the command stream — Netty fails to do so for `DefaultSmtpRequest` parameters.
**Failed invariant (Protobuf analog):** A TextFormat string literal must not contain unescaped control characters that a re-parsing tool, log viewer, or downstream text-format consumer could interpret as a new field/line boundary — this Java printer path fails to escape `\r`.

**Attacker-controlled value:** a `string` field value coming from parsed, otherwise-valid Protobuf/ProtoJSON input, later re-emitted by an application via `TextFormat.printer().escapingNonAscii(false)...printToString(message)`.

**Missing check:** no replacement/escape of `\r` (unlike `\n`, `"`, `\\`), even though the sibling function `TextFormatEscaper.escapeText` and every other language's implementation treat `\r` identically to `\n`.

### Impact Explanation
`escapingNonAscii(false)` is a public, documented `Printer` configuration option (`TextFormat.Printer#escapingNonAscii`), not an internal/debug-only code path, so any consuming application that (a) parses untrusted Protobuf/ProtoJSON containing attacker-controlled string fields and (b) re-serializes the message with this printer configuration to logs, config files, or any system that is later re-parsed as text (line-oriented logs, other TextFormat consumers, `.textproto` configs) can have raw `\r` bytes injected into the output. Depending on the consumer, this can:
- Corrupt line-based log parsing/SIEM ingestion (log forging via `\r` overwriting the start of a line on terminals/log viewers that don't normalize CR), analogous to how the Netty CRLF let attacker content masquerade as separate, trusted protocol commands.
- If the printed text is subsequently fed into another TextFormat parser or line-splitting tool that treats bare `\r` as a record separator, allow field/record boundary spoofing.

This is a text-format hardening gap, not a memory-safety or binary-wire-format issue, and it only affects the Java implementation's non-default `escapingNonAscii(false)` mode — the default `escapingNonAscii(true)` path (`TextFormatEscaper.escapeText`) is unaffected because it escapes `\r` correctly. Impact is therefore bounded to Medium: no attacker code execution, no memory corruption, and TextFormat itself is documented as unsuitable as a wire/security boundary format ("Systems processing untrusted inputs should strongly prefer to use Binary format instead"). [3](#0-2) 

### Likelihood Explanation
Likelihood is moderate: reaching the flaw requires the application to explicitly opt into `escapingNonAscii(false)` (not the default) and to further pipe the printed TextFormat string into a context where a bare `\r` matters (log line splitting, secondary parsing). This is a plausible but non-default configuration, and the impact is confined to text/log integrity, not memory safety.

### Recommendation
Make the `escapingNonAscii(false)` branch of `printFieldValue` also escape `\r` (and ideally reuse the same replacement table as `TextFormatEscaper.escapeText`/`escapeBytes` instead of a bespoke two-character replace), e.g.:
```java
: escapeDoubleQuotesAndBackslashes((String) value)
    .replace("\r", "\\r")
    .replace("\n", "\\n"));
```
Consider deprecating the ad hoc `escapeDoubleQuotesAndBackslashes` + manual `\n` replace in favor of always routing through `TextFormatEscaper`, which already correctly escapes all control characters including `\r`. [4](#0-3) 

### Proof of Concept
```java
TestAllTypes message =
    TestAllTypes.newBuilder()
        .setOptionalString("legit-value\rINJECTED: true")
        .build();

String out = TextFormat.printer().escapingNonAscii(false).printToString(message);
// Expected (secure): optional_string: "legit-value\rINJECTED: true"
// Actual (vulnerable): optional_string: "legit-value<RAW CR>INJECTED: true"
```
The existing test suite only exercises `\n` handling for this printer mode (`testPrintToUnicodeStringWithNewlines`), never asserting `\r` escaping: [5](#0-4) 

This confirms the code path exists, is reachable via a public API, and is untested for `\r`, but I was not able to fully trace, within the remaining investigation budget, how a specific downstream consumer's re-parser would interpret the injected `\r` (i.e., I could not confirm the TextFormat tokenizer's exact handling of a raw CR inside a quoted string during `TextFormat.merge`, since I ran out of tool calls before reading the `Tokenizer`/string-consumption logic). That remaining verification would be needed to fully characterize whether re-parsing TextFormat output containing this defect can itself be exploited for field-injection, versus only affecting log/line-oriented consumers.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L36-40)
```java
 * <p>Unlike the Binary and ProtoJSON formats, Text Format is not designed to be used as a wire
 * format; instead it is intended for human-in-the-loop configuration use-cases.
 *
 * <p>Systems processing untrusted inputs should strongly prefer to use Binary format instead. If a
 * textual format of untrusted inputs is required, consider using ProtoJSON format instead.
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
