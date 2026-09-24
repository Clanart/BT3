Based on the investigation, I found a legitimate analog in Protobuf's Java `TextFormat` printer, and it is meaningfully distinct in strength from the C++ implementation, which already hardens against this class of issue.

### Title
Java TextFormat UTF-8 string printing does not escape non-newline control characters, enabling terminal/log injection - (File: `java/core/src/main/java/com/google/protobuf/TextFormat.java`)

### Summary
The Rocketpool report's core failure is: attacker-controlled string data (timezone, email) is validated only loosely (min length 4) and is later displayed via `fmt.Printf` without sanitizing control characters, enabling terminal spoofing/log injection. The Protobuf analog transfers the same invariant failure to the Java `TextFormat` printer's UTF-8-preserving code path: wire-parsed `string` fields are only checked for UTF-8 structural validity (not printability), and when printed via the non-ASCII-escaping branch, most control characters pass through unescaped into terminal/log output.

### Finding Description
Protobuf's own invariant for `string` fields is only "structurally valid UTF-8" — enforced by `utf8_range::IsStructurallyValid` in the parser [1](#0-0)  and by `VerifyUTF8`/`VerifyUtf8String` on the classic parse path [2](#0-1) . This validation says nothing about printability: control characters such as `\r`, `\t`, `ESC (0x1B)`, and other C0 codes are valid UTF-8 and pass parsing unmodified, exactly like Rocketpool's 4-character-minimum check said nothing about content format.

In the Java `TextFormat.Printer.printFieldValue` STRING case, when the printer is configured to preserve non-ASCII bytes (`escapeNonAscii == false`, the mode used by UTF-8-preserving printing such as `Utf8DebugString()`), only quotes, backslashes, and `\n` are escaped: [3](#0-2) 

This is weaker than:
- The fully hardened `TextFormatEscaper.escapeText`/`escapeBytes` path used when `escapeNonAscii == true`, which escapes all non-printable ASCII (bell, backspace, tab, LF, VT, CR, form-feed) plus octal-escapes everything else [4](#0-3) .
- The C++ implementation's equivalent logic, which explicitly treats any character `< 32` (all C0 controls, including `\r`, `ESC`) or `> 126` or quote/backslash as "needs escape," and applies `CEscape` to it before printing, both in the fast printer [5](#0-4)  and in the explicitly named `HardenedPrintString` used for guarding against malformed/adversarial byte sequences [6](#0-5) [7](#0-6) .

So the Java non-ASCII-escaping branch is a genuinely weaker sink than both its own sibling branch and the analogous C++ code, and it directly transfers the Rocketpool pattern: attacker-controlled, format-unchecked string data flows to a `fmt`/print-style sink (`generator.print`) with insufficient control-character sanitization.

### Impact Explanation
An ordinary client can submit a bounded Protobuf message with a `string` field whose bytes are valid UTF-8 but include `\r` (carriage return), `ESC (0x1B)` (ANSI escape start), or other C0 controls other than `\n`. Any consuming Java application that parses this trusted-schema message and displays it with the UTF-8-preserving printer (e.g., `TextFormat.printer().escapingNonAscii(false)` / `Utf8DebugString()`-style output) will emit these raw control bytes to its output stream. If that output reaches a terminal or an ANSI-aware log viewer, the attacker can overwrite prior lines (`\r`), inject fake log lines, or manipulate terminal state via escape sequences — the same "false information injection"/"terminal trust exploitation" impact called out in the original report. This is a display-integrity/log-injection issue, not memory corruption; consistent with the report's own resolution being addressed as an output-sanitization gap rather than a critical vulnerability.

### Likelihood Explanation
Requires: (1) an application parsing untrusted-but-valid Protobuf messages with attacker-controlled string fields (fully within the stated threat model — bounded, well-formed input through a public parse API), and (2) that application choosing the UTF-8-preserving text-dump path rather than the default fully-escaping path. This is a real, reachable but non-default configuration, making likelihood moderate rather than high.

### Recommendation
Harden the `escapeNonAscii == false` branch in `TextFormat.printFieldValue` (STRING case) to escape all C0 control characters and DEL (0x7F), not just quotes/backslash/`\n`, mirroring the C++ `DefinitelyNeedsEscape`/`HardenedPrintString` logic. Document in the Java API/security notes that `string` field UTF-8 validation guarantees only structural validity, not printability, and that consumers must not assume control-character-free content when logging/displaying parsed messages.

### Proof of Concept
1. Construct a message with a `string` field set to `"OK\x1b[2K\rFAKE STATUS: Trusted"` (valid UTF-8; contains ESC 0x1B and CR).
2. Serialize with a standard `ParseFrom`/`SerializeToString` round trip (passes UTF-8 validation, since ESC/CR are valid ASCII bytes).
3. In a Java consumer, call `TextFormat.printer().escapingNonAscii(false).printToString(msg)` (or the legacy `Utf8DebugString()`), matching the `printFieldValue` STRING branch at [3](#0-2) .
4. Observe the output string contains the raw `\x1b[2K\r` bytes unescaped; when written to a terminal or ANSI-interpreting log viewer, it clears/overwrites the current line and displays attacker-controlled "FAKE STATUS" text in place of legitimate output — reproducing the report's terminal-trust-exploitation impact.

**Caveat on completeness:** I was unable to fully trace, within the available tool budget, every call site that ultimately uses `escapeNonAscii == false` at the API surface level (e.g., confirming which public `TextFormat` methods set this flag by default) due to a tool failure in the final investigation round. The vulnerable code path and the weaker-than-C++ escaping logic are confirmed directly from source; the exact set of default-configuration callers should be verified in a follow-up before treating this as conclusively reachable via a specific named public method.

### Citations

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L2503-2515)
```text
bool TcParser::MpVerifyUtf8(absl::string_view wire_bytes,
                            const TcParseTableBase* table,
                            const FieldEntry& entry, uint16_t xform_val) {
  if (xform_val == field_layout::kTvUtf8) {
    if (!utf8_range::IsStructurallyValid(wire_bytes)) {
      PrintUTF8ErrorLog(MessageName(table), FieldName(table, &entry), "parsing",
                        false);
      return false;
    }
    return true;
  }
  return true;
}
```

**File:** src/google/protobuf/wire_format.cc (L522-532)
```text
        bool strict_utf8_check = field->requires_utf8_validation();
        std::string value;
        if (!WireFormatLite::ReadString(input, &value)) return false;
        if (strict_utf8_check) {
          if (!WireFormatLite::VerifyUtf8String(value.data(), value.length(),
                                                WireFormatLite::PARSE,
                                                field->full_name())) {
            return false;
          }
        } else {
        }
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L633-639)
```java
        case STRING:
          generator.print("\"");
          generator.print(
              escapeNonAscii
                  ? TextFormatEscaper.escapeText((String) value)
                  : escapeDoubleQuotesAndBackslashes((String) value).replace("\n", "\\n"));
          generator.print("\"");
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormatEscaper.java (L12-30)
```java
/**
 * Provide text format escaping of proto instances. These ASCII characters are escaped:
 *
 * <ul>
 *   <li>ASCII #7 (bell) --> \a
 *   <li>ASCII #8 (backspace) --> \b
 *   <li>ASCII #9 (horizontal tab) --> \t
 *   <li>ASCII #10 (linefeed) --> \n
 *   <li>ASCII #11 (vertical tab) --> \v
 *   <li>ASCII #13 (carriage return) --> \r
 *   <li>ASCII #12 (formfeed) --> \f
 *   <li>ASCII #34 (apostrophe) --> \'
 *   <li>ASCII #39 (straight double quote) --> \"
 *   <li>ASCII #92 (backslash) --> \\
 *   <li>ASCII characters besides those three which are in the range [32..127] inclusive are output
 *       as is, unescaped.
 *   <li>All other bytes are escaped as octal sequences. If we are printing text, we convert to
 *       UTF-8 and print any high codepoints as their UTF-8 encoded units in octal escapes.
 * </ul>
```

**File:** src/google/protobuf/text_format.cc (L1818-1836)
```text
bool DefinitelyNeedsEscape(unsigned char ch) {
  if (ch >= 0x80) {
    return false;  // High byte; no escapes necessary if UTF-8 is valid.
  }

  if (!absl::ascii_isprint(ch)) {
    return true;  // Unprintable characters need escape.
  }

  switch (ch) {
    case '\"':
    case '\'':
    case '\\':
      // These characters need escapes despite being printable.
      return true;
  }

  return false;
}
```

**File:** src/google/protobuf/text_format.cc (L1877-1899)
```text
void TextFormat::Printer::HardenedPrintString(
    absl::string_view src, TextFormat::BaseTextGenerator* generator) {
  // Print as UTF-8, while guarding against any invalid UTF-8 in the string
  // field.
  //
  // If in the future we have a guaranteed invariant that invalid UTF-8 will
  // never be present, we could avoid the UTF-8 check here.

  generator->PrintLiteral("\"");
  while (!src.empty()) {
    size_t n = SkipPassthroughBytes(src);
    if (n != 0) {
      generator->PrintString(src.substr(0, n));
      src.remove_prefix(n);
      if (src.empty()) break;
    }

    // If repeated calls to CEscape() and PrintString() are expensive, we could
    // consider batching them, at the cost of some complexity.
    generator->PrintString(absl::CEscape(src.substr(0, 1)));
    src.remove_prefix(1);
  }
  generator->PrintLiteral("\"");
```

**File:** src/google/protobuf/text_format.cc (L2216-2238)
```text
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
