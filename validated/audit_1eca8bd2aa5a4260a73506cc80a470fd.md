### Title
ProtoJSON map-key parse error embeds raw attacker-controlled key string unescaped in `absl::Status` message - ([File: src/google/protobuf/json/internal/parser.cc])

### Summary
When ProtoJSON parses a `map<bool, V>` field, an invalid boolean key produces an error message that embeds the decoded JSON key string verbatim via `%s`, with no escaping of control characters, newlines, or terminal-escape sequences. This mirrors the Go `crypto/tls` ALPN issue (CVE-2025-58189), where attacker-supplied protocol-negotiation strings were placed unescaped into a handshake error.

### Finding Description
The Go advisory's failed invariant is: error strings surfaced from a parser must not embed raw, unescaped attacker-controlled bytes, because such strings routinely flow into logs, terminals, or downstream error handling that assumes "safe" diagnostic text.

In this checkout, `ParseMapKey<Traits>` in `src/google/protobuf/json/internal/parser.cc:627-635` handles a `bool` map key:
```cpp
case FieldDescriptor::TYPE_BOOL: {
  if (key.value == "true") { ... }
  else if (key.value == "false") { ... }
  else {
    return key.loc.Invalid(absl::StrFormat("expected bool string, got '%s'",
                                           key.value.AsView()));
  }
``` [1](#0-0) 

`key.value` is a `MaybeOwnedString` produced by the JSON lexer after unescaping standard JSON string escapes (e.g. `\n`, `\u001b`) into raw bytes — see `JsonLexer::ParseUtf8Slow`, which decodes `\uXXXX` escapes directly into the byte buffer that becomes `key.value` [2](#0-1) . This decoded string is attacker-fully-controlled: any JSON object key under a `bool`-keyed map field reaches this code path with arbitrary bytes, including newlines and ANSI/terminal escape sequences, since the only content restriction is "not the literal `true` or `false`".

That raw string is inserted with `%s` into the diagnostic text with no `CEscape`-style transform, unlike the equivalent TextFormat/DebugString printers in this same repo, which explicitly call `absl::CEscape` or `TextFormat::Printer::HardenedPrintString` before putting field *values* into human-readable output [3](#0-2) . The JSON error path has no equivalent escaping step: `Location::Invalid` (in `lexer.h`) and `JsonLocation::Invalid` only add position metadata and (for the top-level "invalid JSON" wrapper) intentionally skip hardening the message text, as the code comment states: *"we intentionally do not harden the 'invalid JSON' part, so that people have a hope of grepping for it in logs."* [4](#0-3)  That comment documents an accepted design tradeoff for the *lexer's own* immediate-syntax-error path (e.g. `unexpected character: '%c'`, a single raw byte) — but the map-key `%s` case in `parser.cc` embeds a full attacker-controlled string, not a single character, and is a distinct call site not covered by that documented tradeoff.

The resulting `absl::Status` (`InvalidArgumentError`) propagates out through the public ProtoJSON parsing entry points (`JsonStringToMessage`/`util::JsonStringToMessage` and language-binding equivalents), which is exactly the "attacker-controlled information reaches an error surface without escaping" pattern from the ALPN report — the ALPN protocol list is analogous to the JSON map key string, and the TLS handshake error is analogous to the `absl::Status` message.

### Impact Explanation
Applications commonly log `absl::Status` messages from failed ProtoJSON parses (for observability/debugging), return them to callers (e.g., as gRPC status details or HTTP error bodies), or render them in terminals/dashboards. An attacker-controlled map key containing newlines can forge fake extra log lines (log injection/log forging); one containing ANSI escape sequences can manipulate terminal output for anyone tailing logs. This is a confidentiality/integrity-adjacent issue for downstream log/monitoring integrity rather than memory safety, consistent with the "Medium" severity of the Go analog. It does not affect message parsing correctness or memory safety of the JSON decoder itself.

### Likelihood Explanation
High likelihood of reachability: any consumer that parses ProtoJSON containing a `map<bool, V>` field and logs/displays parse errors is affected merely by sending one malformed request. No privileged access or malicious schema is required — this fits the "ordinary client sending bounded ProtoJSON through a supported public parse API" threat model exactly.

### Recommendation
Escape or sanitize `key.value.AsView()` (e.g., via `absl::CEscape` or an equivalent control-character escaper) before interpolating it into the `"expected bool string, got '%s'"` diagnostic, consistent with how `TextFormat::FastFieldValuePrinter::PrintString` and `HardenedPrintString` already treat untrusted field values elsewhere in the codebase. Audit other `absl::StrFormat`/`StrCat` call sites in `src/google/protobuf/json/internal/parser.cc` and `lexer.cc` that embed decoded JSON string content (not just single characters) into `Invalid(...)` messages for the same pattern.

### Proof of Concept
Given a proto schema with `map<bool, string> flag_map;`, parsing the following ProtoJSON payload through the public `JsonStringToMessage` API:
```json
{"flagMap": {"true\n[FAKE LOG] admin login succeeded\u001b[0m": "x"}}
```
produces an `absl::Status` (`InvalidArgumentError`) whose message text contains the literal decoded bytes `expected bool string, got 'true\n[FAKE LOG] admin login succeeded\x1b[0m'` — i.e., an embedded newline and an ANSI reset escape sequence, unescaped, exactly as attacker-supplied. This was traced statically to `ParseMapKey`'s `TYPE_BOOL` branch [5](#0-4) ; I was not able to execute this against a live build within this environment, so the concrete resulting status string is derived from static code-path analysis rather than a captured runtime trace — this should be confirmed by running the described payload through `google::protobuf::json::JsonStringToMessage` in a Devin session with build tooling available.

### Citations

**File:** src/google/protobuf/json/internal/parser.cc (L627-635)
```text
    case FieldDescriptor::TYPE_BOOL: {
      if (key.value == "true") {
        Traits::SetBool(key_field, entry, true);
      } else if (key.value == "false") {
        Traits::SetBool(key_field, entry, false);
      } else {
        return key.loc.Invalid(absl::StrFormat("expected bool string, got '%s'",
                                               key.value.AsView()));
      }
```

**File:** src/google/protobuf/json/internal/lexer.cc (L84-104)
```text
absl::Status JsonLocation::Invalid(absl::string_view message,
                                   SourceLocation sl) const {
  // NOTE: we intentionally do not harden the "invalid JSON" part, so that
  // people have a hope of grepping for it in logs. That part is easy to
  // commit to, as stability goes.
  //
  // This copies the error twice. Because this is the "unhappy" path, this
  // function is cold and can afford the waste.
  std::string status_message = "invalid JSON";
  std::string to_obfuscate;
  if (path != nullptr) {
    absl::StrAppend(&to_obfuscate, " in ");
    path->Describe(to_obfuscate);
    to_obfuscate.push_back(',');
  }
  absl::StrAppendFormat(&to_obfuscate, " near %zu:%zu (offset %zu): %s",
                        line + 1, col + 1, offset, message);
  HardenAgainstHyrumsLaw(to_obfuscate, status_message);

  return absl::InvalidArgumentError(std::move(status_message));
}
```

**File:** src/google/protobuf/json/internal/lexer.cc (L481-501)
```text
      case '\\': {
        RETURN_IF_ERROR(stream_.BufferAtLeastOne());

        char c = stream_.PeekChar();
        RETURN_IF_ERROR(Advance(1));
        if (c == 'u' ||
            (c == 'U' && options_.allow_legacy_nonconformant_behavior)) {
          // Ensure there is actual space to scribble the UTF-8 onto.
          on_heap.resize(on_heap.size() + 4);
          auto written = ParseUnicodeEscape(&on_heap[on_heap.size() - 4]);
          RETURN_IF_ERROR(written.status());
          on_heap.resize(on_heap.size() - 4 + *written);
        } else {
          char escape = ParseSimpleEscape(
              c, options_.allow_legacy_nonconformant_behavior);
          if (escape == 0) {
            return Invalid(absl::StrFormat("invalid escape char: '%c'", c));
          }
          on_heap.push_back(escape);
        }
        break;
```

**File:** src/google/protobuf/text_format.cc (L2227-2238)
```text
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
