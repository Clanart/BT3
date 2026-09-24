Confirmed: `HardenAgainstHyrumsLaw` (src/google/protobuf/json/internal/lexer.cc:48-79) only injects random whitespace padding into the message — it copies every input character (including raw control/escape bytes) through unchanged via `out.push_back(c)` at line 69. It performs no CWE-117-style neutralization of control characters (no CRLF stripping, no ANSI/CSI escape filtering). This confirms the log-injection analog described below.

### Title
Log Injection via Unsanitized JSON Parse-Error Content Embedded in `absl::InvalidArgumentError` Messages - (File: src/google/protobuf/json/internal/lexer.cc)

### Summary
`JsonLocation::Invalid()` builds a detailed diagnostic string that includes the raw, attacker-controlled byte/text encountered while lexing ProtoJSON input (e.g., `absl::StrFormat("unexpected character: '%c'", c)` at `lexer.cc:136`), then passes it through `HardenAgainstHyrumsLaw` and returns it as the message of an `absl::InvalidArgumentError` at `lexer.cc:84-104`. `HardenAgainstHyrumsLaw` (`lexer.cc:48-79`) copies every character of the untrusted text through unchanged aside from randomized whitespace padding — it never strips or escapes control characters (CR, LF, ANSI/CSI escape sequences). If the consuming application logs this returned `Status` (a very common pattern, e.g. `LOG(ERROR) << status;`), attacker-supplied bytes from the JSON payload flow directly into the log stream unsanitized.

### Finding Description
The Kibana CVE (CWE-117) failed invariant is: content derived from untrusted input must be neutralized before being written into a text log stream that may later be rendered by a terminal interpreting control sequences. In this Protobuf analog, the equivalent invariant is that error/diagnostic strings built from attacker-controlled JSON bytes and surfaced through the public `JsonStringToMessage`/`JsonToBinaryStream` parsing API (`src/google/protobuf/json/internal/parser.cc`) must not carry raw control bytes into a status message that applications conventionally log verbatim.

Trace: `JsonLexer::PeekKind()` (`lexer.cc:106-138`) reads a single raw byte `c` from attacker-controlled JSON input and, on an unexpected character, calls `Invalid(absl::StrFormat("unexpected character: '%c'", c))`. `JsonLocation::Invalid` (`lexer.cc:84-104`) appends this attacker byte, uninspected, into `to_obfuscate`, and hands it to `HardenAgainstHyrumsLaw`, whose only transformation is randomized whitespace insertion (`lexer.cc:66-78`) — every other byte, including control characters such as `\r`, `\n`, or ESC (`0x1B`), passes straight through via `out.push_back(c)`. The resulting string becomes the message of the returned `absl::InvalidArgumentError`, which is the value ultimately returned to the caller of the public JSON parsing entry points.

This differs from the TextFormat parser, where the tokenizer (`src/google/protobuf/io/tokenizer.cc`) explicitly rejects raw control characters anywhere in the source text before any diagnostic is constructed (confirmed by the test `TextFormatParserTest.FailsOnTokenizationError`, `text_format_unittest.cc:2728-2740`, which expects `"Invalid control characters encountered in text."`), and identifiers/field names logged via `ReportErrorImpl`/`ReportWarning` (`text_format.cc:342-357`, `474-494`) are drawn from a restricted identifier character set, not arbitrary attacker bytes. No equivalent control-character rejection exists in the JSON lexer's error-message construction path.

### Impact Explanation
If a consuming application logs the `absl::Status`/exception returned by the JSON parsing API (a standard practice for diagnosing malformed client input), an attacker who controls the JSON payload can inject raw control bytes (e.g., ANSI escape sequences, CR to overwrite previous log lines) into that log entry. When such logs are viewed in a terminal or log viewer that interprets control sequences, this can be used to forge, hide, or tamper with the visual representation of log data (CAPEC-93), matching the impact class of the Kibana CVE. This does not achieve memory corruption or code execution in Protobuf itself — the impact is confined to log integrity/display-forging in the consuming application, which is the same class of impact assessed for the original Kibana CVE (log forging/tampering), not a memory-safety issue.

### Likelihood Explanation
Likelihood is moderate: exploitation requires (1) an ordinary client able to submit malformed ProtoJSON that triggers this specific lexer error path, and (2) the consuming application choosing to log the returned status text verbatim to a log sink later viewed on a terminal — both conditions plausible in a typical JSON API gateway using `google::protobuf::util::JsonStringToMessage`. This is a lower-severity, easily reproducible condition, not requiring any bypass of size/depth/type-registry checks, since it operates purely on the diagnostic-string construction path, independent of the parse's success/failure semantics.

### Recommendation
Apply the same control-character neutralization used by the TextFormat tokenizer to the JSON lexer's error-message construction: before embedding the raw offending byte/text into the `to_obfuscate`/`status_message` string in `JsonLocation::Invalid` (`lexer.cc:84-104`), escape or strip non-printable/control characters (e.g., via `absl::CEscape` or an explicit control-character filter) rather than relying solely on `HardenAgainstHyrumsLaw`, which only obfuscates whitespace and passes all other bytes through unchanged (`lexer.cc:66-78`).

### Proof of Concept
Feeding malformed ProtoJSON containing a control byte at the point an unexpected character is expected reproduces the issue conceptually:
```cpp
std::string json = "{\"foo\": \x1b[31mFAKE-ERROR\x1b[0m";  // ESC[31m ... ESC[0m raw in payload position that triggers "unexpected character"
google::protobuf::util::JsonParseOptions options;
MyMessage msg;
absl::Status s = google::protobuf::util::JsonStringToMessage(json, &msg, options);
LOG(ERROR) << s;  // consuming application's conventional error-logging pattern
```
The resulting logged status message embeds the raw `\x1b[31m...\x1b[0m` control sequence returned by `JsonLocation::Invalid`, since `HardenAgainstHyrumsLaw` does not filter it out — only actually running this against the checkout's JSON lexer would confirm the exact byte-for-byte propagation; this was not executed in this environment, so the PoC is a code-level trace to the exact functions and lines shown above, not an executed test result. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** src/google/protobuf/json/internal/lexer.cc (L48-79)
```text
void HardenAgainstHyrumsLaw(absl::string_view to_obfuscate, std::string& out) {
  // Get some simple randomness from ASLR, which is enabled in most
  // environments. Our goal is to be annoying, not secure.
  static const void* const kAslrSeed = &kAslrSeed;
  // Per-call randomness from a relaxed atomic.
  static std::atomic<uintptr_t> kCounterSeed{0};

  constexpr uint64_t kA = 0x5851f42d4c957f2dull;
  constexpr uint64_t kB = 0x14057b7ef767814full;

  uint64_t state = absl::bit_cast<uintptr_t>(kAslrSeed) + kB +
                   kCounterSeed.fetch_add(1, std::memory_order_relaxed);
  auto rng = [&state, &kA, &kB] {
    state = state * kA + kB;
    return absl::rotr(static_cast<uint32_t>(((state >> 18) ^ state) >> 27),
                      state >> 59);
  };
  (void)rng();  // Advance state once.

  out.reserve(to_obfuscate.size() + absl::c_count(to_obfuscate, ' '));
  for (char c : to_obfuscate) {
    out.push_back(c);
    if (c != ' ' || rng() % 3 != 0) {
      continue;
    }

    size_t count = rng() % 2 + 1;
    for (size_t i = 0; i < count; ++i) {
      out.push_back(' ');
    }
  }
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

**File:** src/google/protobuf/json/internal/lexer.cc (L106-138)
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
}
```

**File:** src/google/protobuf/text_format.cc (L342-357)
```text
void ReportErrorImpl(int line, int col, absl::string_view message,
                     const Descriptor* root_message_type,
                     io::ErrorCollector* error_collector) {
  if (error_collector == nullptr) {
    if (line >= 0) {
      ABSL_LOG(ERROR) << "Error parsing text-format "
                      << root_message_type->full_name() << ": " << (line + 1)
                      << ":" << (col + 1) << ": " << message;
    } else {
      ABSL_LOG(ERROR) << "Error parsing text-format "
                      << root_message_type->full_name() << ": " << message;
    }
  } else {
    error_collector->RecordError(line, col, message);
  }
}
```

**File:** src/google/protobuf/text_format_unittest.cc (L2728-2740)
```text
TEST_F(TextFormatParserTest, FailsOnTokenizationError) {
  {
    absl::ScopedMockLog log(absl::MockLogDefault::kDisallowUnexpected);
    EXPECT_CALL(log,
                Log(absl::LogSeverity::kError, testing::_,
                    "Error parsing text-format proto2_unittest.TestAllTypes: "
                    "1:1: Invalid control characters encountered in text."))
        .Times(1);
    log.StartCapturingLogs();
    unittest::TestAllTypes proto;
    EXPECT_FALSE(TextFormat::ParseFromString("\020", &proto));
  }
}
```
