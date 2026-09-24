### Title
JSON parsing error/status messages embed raw attacker-controlled bytes (including CR/LF and other control characters) unescaped - ([File: src/google/protobuf/json/internal/lexer.cc])

### Summary
Protobuf's C++ ProtoJSON parser (`json_internal::JsonLexer`/`JsonLocation::Invalid`) builds `absl::Status` error messages by directly interpolating raw bytes and substrings taken from the attacker-supplied JSON input (e.g. an unexpected character, an unparsable map-key value) via `absl::StrFormat("%c"/"%s", ...)`, with no escaping of control characters such as `\r`/`\n`. Applications that log these `absl::Status` messages (a standard practice, e.g. `LOG(ERROR) << status`) can have forged/injected log lines, analogous to the morgan `:remote-user` CRLF log-injection (CWE-117), because the attacker fully controls the byte that lands, unescaped, in the message.

### Finding Description
`JsonLocation::Invalid` constructs the final status text as: [1](#0-0) 

Note the explicit design decision on line 86-88: *"we intentionally do not harden the 'invalid JSON' part, so that people have a hope of grepping for it in logs"* — i.e., part of the message is deliberately left un-obfuscated/un-escaped, and the `HardenAgainstHyrumsLaw` transform that is applied to the rest is not real escaping either — it just randomly inserts extra spaces to defeat brittle string matching; it does not strip or escape `\r`/`\n`/other control characters. The `message` parameter passed into `Invalid()` frequently contains raw attacker bytes, e.g.:

- A raw single character from the input stream, unescaped, via `%c`: [2](#0-1) 
(see line 136: `return Invalid(absl::StrFormat("unexpected character: '%c'", c));`)

- A raw attacker-controlled substring embedded via `%s` when a map key fails validation: [3](#0-2) 
(`key.value.AsView()` is the literal bytes taken from the JSON input's map key text.)

Neither of these call sites escapes `\r`, `\n`, or other control bytes before they are folded into the `absl::Status` message string that is ultimately returned to (and, in typical applications, logged by) the caller of the public parsing entry points (`JsonStringToMessage`, `JsonToBinaryStream`, etc.).

This mirrors morgan's flaw precisely: an attacker-controlled value (`:remote-user` in morgan; a JSON character/substring here) is written into a text sink used for logging/diagnostics without neutralizing CR/LF control characters, breaking the invariant that "one produced record == one log line."

### Impact Explanation
If a consuming service logs the `absl::Status` returned from ProtoJSON parsing failures (very common practice for diagnosing malformed requests), an attacker who controls the JSON payload can inject `\r`/`\n` sequences into that log line, forging fake subsequent log entries, splitting/altering the visual/structural integrity of the log, and potentially spoofing benign-looking log lines to mislead operators or downstream log parsers/SIEM rules. This matches CWE-117 / the morgan advisory's impact class: no confidentiality/availability impact, but integrity impact on log data (CVSS I:L in the original advisory). It does not grant code execution, memory corruption, or any deeper protobuf-internal compromise — it is purely a log-integrity issue confined to the diagnostic string protobuf hands back to the embedding application.

### Likelihood Explanation
Likelihood is Medium: the vulnerable code path is on the public, commonly used `JsonStringToMessage`/`JsonToBinaryStream` JSON-parsing entry points, reachable by any attacker who can submit malformed/adversarial ProtoJSON to a service that accepts protobuf-JSON input (an ordinary bounded request, no privileged access needed) — exactly the assumed attacker model. The actual exploitation, however, depends on the consuming application choosing to log the raw `absl::Status` message rather than a sanitized/structured error representation, which is a common but not universal pattern.

### Recommendation
- Escape or strip control characters (`\r`, `\n`, and other C0 control bytes) from any attacker-supplied fragment (single characters via `%c`, substrings via `%s`) before splicing it into `JsonLocation::Invalid()`'s `message` argument, in both `lexer.cc` and `parser.cc`.
- Extend `HardenAgainstHyrumsLaw` (or add a dedicated sanitization step) to genuinely neutralize control characters rather than only inserting cosmetic whitespace noise, while keeping the message useful for legitimate debugging (e.g., using `absl::CEscape`-style escaping of any embedded raw bytes, consistent with what `TextFormat`/`upb` JSON encoders already do for actual field *values* — see `src/google/protobuf/json/internal/writer.cc` `MustEscape()`).
- Document, in the public JSON API, that returned `absl::Status` messages may contain attacker-influenced content and that callers should not log them verbatim without their own sanitization if compliance/log-integrity guarantees are required.

### Proof of Concept
Given a JSON payload where the parser needs to report a lexing/validation error and the offending byte/substring is attacker-chosen, e.g.:
```
{"bool_map": {"\r\nFAKE LOG LINE INJECTED\r\n": 1}}
```
Calling the public parse API (e.g., `google::protobuf::json::JsonStringToMessage(payload, &msg)`) on a message containing a `bool` map field triggers the `TYPE_BOOL` map-key validation failure path: [3](#0-2) 
which builds the message `"expected bool string, got '\r\nFAKE LOG LINE INJECTED\r\n'"` and returns it as the text of an `absl::InvalidArgumentError` via `JsonLocation::Invalid`. If the calling service does `LOG(ERROR) << status;` on parse failure (a routine diagnostic pattern), the resulting log stream contains the attacker's injected `\r\n`-delimited fake line, corrupting the log structure — the direct analog of the morgan `:remote-user` CRLF log-forging advisory. (This trace is based on static code reading of the cited files; no live execution/log capture was performed in this session, so the exact final log-line formatting depends on the logging sink used by the embedding application.)

### Citations

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

**File:** src/google/protobuf/json/internal/lexer.cc (L106-137)
```text
absl::StatusOr<JsonLexer::Kind> JsonLexer::PeekKind() {
  RETURN_IF_ERROR(SkipToToken());
  char c = stream_.PeekChar();
  switch (c) {
    case '{':
      return JsonLexer::kObj;
    case '[':
      return JsonLexer::kArr;
    case '"':
    case '\'':
      return JsonLexer::kStr;
    case '-':
    case '0':
    case '1':
    case '2':
    case '3':
    case '4':
    case '5':
    case '6':
    case '7':
    case '8':
    case '9':
      return JsonLexer::kNum;
    case 't':
      return JsonLexer::kTrue;
    case 'f':
      return JsonLexer::kFalse;
    case 'n':
      return JsonLexer::kNull;
    default:
      return Invalid(absl::StrFormat("unexpected character: '%c'", c));
  }
```

**File:** src/google/protobuf/json/internal/parser.cc (L627-636)
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
      break;
```
