### Title
TextFormat parser echoes raw, unescaped token bytes (including control/ANSI-escape characters) into error messages - (File: `src/google/protobuf/text_format.cc`)

### Summary
CVE-2018-1000021 describes GIT printing attacker-controlled, unescaped bytes (server-supplied ref names / commit metadata) to the user's terminal, allowing ANSI/terminal escape-sequence injection because GIT's client failed to sanitize untrusted strings before display. The transferable invariant is: *any attacker-influenced string that is surfaced to a human-readable channel (terminal/log) must be escaped so that control bytes (e.g. ESC `0x1B`) cannot reach the display raw.* Protobuf's TextFormat/JSON/UPB printers consistently enforce this invariant for legitimate field-value output via `TextFormat::Printer::HardenedPrintString`, `absl::CEscape`, `DefinitelyNeedsEscape`, and equivalent logic in Java/Python/C#/UPB. However, the TextFormat **parser's error-reporting path** does not apply this same escaping when it echoes the raw token text of a malformed input back into a `ParseException`/`ErrorCollector` message.

### Finding Description
`io::Tokenizer::ConsumeString()` (`src/google/protobuf/io/tokenizer.cc:367-430`) accepts a quoted string literal and only special-cases `\0`, `\n` (when not in multiline mode) and `\` escape sequences; any other raw byte — including ASCII control characters such as ESC (`0x1B`), which forms the basis of ANSI escape sequences — falls into the `default:` branch and is simply consumed as part of the token’s raw text: [1](#0-0) 

This differs from the tokenizer’s general dispatch loop, `Tokenizer::Next()`, which *does* reject raw unprintable/control bytes for identifiers, numbers, and symbols via the `kUnprintable` check and emits `"Invalid control characters encountered in text."`: [2](#0-1) 

Crucially, that unprintable-character guard is applied only *before* the token-type dispatch decides it's a string; once inside `ConsumeString()`, raw control bytes are accepted unchecked. The `Token::text` field is documented as holding the exact, still-escaped/quoted text as it appeared in the input: [3](#0-2) 

`TextFormat::Parser` then uses this raw `tokenizer_.current().text` directly (without any `CEscape`/`HardenedPrintString`-style sanitization) when constructing human-readable parse-error strings, e.g. when an enum field's value token is not a recognized identifier or integer: [4](#0-3) 

Similarly, `SkipFieldValue()` builds error text directly from `tokenizer_.current().text`: [5](#0-4) 

This is the exact asymmetry the CVE analog requires: every *output* path (`TextFormat::Printer::HardenedPrintString`, `FastFieldValuePrinter::PrintString`, JSON `MustEscape`/`WriteEscapedUtf8`, UPB's `_upb_HardenedPrintString`) rigorously escapes control bytes before display, but the *parser's diagnostic* path does not apply the same treatment to attacker-supplied string-literal content it echoes back. [6](#0-5) [7](#0-6) 

### Impact Explanation
If an application parses attacker-supplied or attacker-influenced TextFormat input (e.g., a text-proto configuration file, a debug/config payload received from a network peer, or user-uploaded text-proto) and — as is extremely common practice — prints the resulting `ParseException`/`ErrorCollector` message directly to a terminal or log viewer, an attacker can smuggle raw ANSI/VT100 escape sequences (cursor movement, screen clearing, OSC/xterm title or clipboard sequences, in some vulnerable terminal emulators even command execution via escape sequence bugs) into that error text. This is a terminal-escape-sequence-injection issue analogous to the GIT CVE: an ordinary client parsing attacker-influenced text and surfacing an unsanitized diagnostic to a terminal. Impact is bounded by what the local terminal emulator does with injected escape sequences (ranges from visual spoofing/DoS to, in vulnerable terminal implementations, more severe effects) — hence Medium severity, matching the original CVE's rating, rather than a Protobuf-native RCE.

### Likelihood Explanation
Reaching this requires only a supported, public entry point (`TextFormat::Parse`/`ParseFromString`) parsing bounded, attacker-influenced text with a normal, trusted schema — no privileged access, hostile schema, or malicious peer needed. The trigger is a malformed field value token (e.g., an enum field given a quoted-string value containing a raw ESC byte, or any parse-failure branch that echoes `tokenizer_.current().text`). This is easy to construct deterministically. The remaining, and significant, caveat is that the *consuming application* must print the error text to a terminal/log for the injection to have any user-visible effect — Protobuf itself has no display surface, so real-world exploitability depends entirely on how the embedding application handles `TextFormat::ParseFromString`'s `ErrorCollector`/exception output (very commonly logged/printed as-is in CLI tools, config validators, etc.).

### Recommendation
Sanitize/escape token text before embedding it in parser diagnostic messages, mirroring the existing print-path hardening:
- In `src/google/protobuf/text_format.cc`, any place that does `absl::StrCat("...", tokenizer_.current().text, "...")` (or similarly interpolates raw token text/field names sourced from input) should route that substring through `absl::CEscape` (or the same `DefinitelyNeedsEscape`/`HardenedPrintString` logic already used for output) before inclusion in the error string.
- Consider hardening `io::Tokenizer::ConsumeString()` itself to flag/report control bytes other than `\n`/`\0` inside string literals (consistent with the `kUnprintable` rejection already applied to bare identifiers/symbols in `Tokenizer::Next()`), since silently accepting raw control bytes inside quoted literals is inconsistent with the rest of the tokenizer's character-class policy.
- Document for embedders that `ErrorCollector`/`ParseException` messages may contain attacker-controlled substrings and should be escaped before being written to a terminal.

### Proof of Concept
Given a schema with an enum field `optional_color: Color` and input:
```
optional_color: "\x1b[31mFAKE ERROR\x1b[0m"
```
Parsing this with `TextFormat::Parser::ParseFromString` reaches the enum branch in `ConsumeFieldValue`, fails to match `TYPE_IDENTIFIER`/`TYPE_INTEGER`, and falls into:
```cpp
ReportError(absl::StrCat("Expected integer or identifier, got: ",
                          tokenizer_.current().text));
``` [8](#0-7) 
Here `tokenizer_.current().text` is the raw, still-quoted token content produced by `ConsumeString()`, which — per its implementation — passes the literal `\x1b` (ESC, `0x1B`) bytes through untouched (they are not `\n`, `\0`, or `\`) into the token text. The resulting `ParseException`/`RecordError` message therefore carries the raw ESC byte sequence. I was not able to execute this locally (no filesystem/terminal access in this environment) to confirm the terminal rendering effect end-to-end; this should be validated with an actual build (e.g., a small harness calling `TextFormat::Parser::ParseFromString` with a `DefaultErrorCollector`/exception path and printing the resulting message to a real terminal) to confirm the escape sequence survives verbatim into the displayed error.

### Citations

**File:** src/google/protobuf/io/tokenizer.cc (L420-427)
```text
      default: {
        if (current_char_ == delimiter) {
          NextChar();
          return;
        }
        NextChar();
        break;
      }
```

**File:** src/google/protobuf/io/tokenizer.cc (L636-648)
```text
    if (LookingAt(kUnprintable) || current_char_ == '\0') {
      AddError("Invalid control characters encountered in text.");
      NextChar();
      // Skip more unprintable characters, too.  But, remember that '\0' is
      // also what current_char_ is set to after EOF / read error.  We have
      // to be careful not to go into an infinite loop of trying to consume
      // it, so make sure to check read_error_ explicitly before consuming
      // '\0'.
      while (TryConsumeOne(kUnprintable) ||
             (!read_error_ && TryConsume('\0'))) {
        // Ignore.
      }

```

**File:** src/google/protobuf/io/tokenizer.h (L126-138)
```text
  // Structure representing a token read from the token stream.
  struct Token {
    TokenType type;
    std::string text;  // The exact text of the token as it appeared in
                       // the input.  e.g. tokens of TYPE_STRING will still
                       // be escaped and in quotes.

    // "line" and "column" specify the position of the first character of
    // the token within the input stream.  They are zero-based.
    int line;
    ColumnNumber column;
    ColumnNumber end_column;
  };
```

**File:** src/google/protobuf/text_format.cc (L1068-1077)
```text
        } else if (LookingAt("-") ||
                   LookingAtType(io::Tokenizer::TYPE_INTEGER)) {
          DO(ConsumeSignedInteger(&int_value, kint32max));
          value = absl::StrCat(int_value);  // for error reporting
          enum_value = enum_type->FindValueByNumber(int_value);
        } else {
          ReportError(absl::StrCat("Expected integer or identifier, got: ",
                                   tokenizer_.current().text));
          return false;
        }
```

**File:** src/google/protobuf/text_format.cc (L1165-1173)
```text
    bool has_minus = TryConsume("-");
    if (!LookingAtType(io::Tokenizer::TYPE_INTEGER) &&
        !LookingAtType(io::Tokenizer::TYPE_FLOAT) &&
        !LookingAtType(io::Tokenizer::TYPE_IDENTIFIER)) {
      std::string text = tokenizer_.current().text;
      ReportError(
          absl::StrCat("Cannot skip field value, unexpected token: ", text));
      return false;
    }
```

**File:** src/google/protobuf/text_format.cc (L1877-1900)
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
}
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
