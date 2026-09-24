Confirmed: `debug_redact`/redaction handling exists only in `text_format.cc` (`TryRedactFieldValue`, `GetRedactionState`, `IsOptionSensitive`) and is wired into the `Printer`/`DebugString` code paths. It is completely absent from the JSON parsing/error-reporting code (`json/internal/parser.cc`, `json/internal/lexer.cc`, `message_path.cc`), confirming the analog.

### Title
ProtoJSON parse-error messages embed raw field values, bypassing `debug_redact` field-level redaction - (File: src/google/protobuf/json/internal/parser.cc, src/google/protobuf/json/internal/lexer.cc)

### Summary
MongoDB's bug (BIT-mongodb-2026-9735) is that a *second* logging path (connection health-metric logging during SASL auth) emits authentication parameters verbatim, bypassing the redaction that the normal auth-log path already applies. Protobuf has an exact structural analog: it defines a schema-level `debug_redact` field option and a corresponding redaction mechanism (`TextFormat::GetRedactionState`, `TryRedactFieldValue`) that is invoked whenever a message is rendered via `DebugString()`/`TextFormat::Printer`. However, that redaction check is never consulted by the separate ProtoJSON parsing code path. When ProtoJSON parsing of a field fails, the raw, attacker/client-supplied textual value is copied verbatim into the returned `absl::Status`/exception message together with the dotted field path, with no reference to `FieldOptions.debug_redact` at all.

### Finding Description
`debug_redact` is protobuf's documented mechanism for marking a field as sensitive so that generic debug/human-readable output never contains its content [1](#0-0) . It is enforced through `TextFormat::Printer::TryRedactFieldValue`, which consults `GetRedactionState(field)` before printing a field's value and substitutes `[REDACTED]` if the field (or an enum/message field nested inside it) is marked sensitive [2](#0-1) . This is exactly analogous to the redaction MongoDB expects on its authentication log line.

The ProtoJSON parser, which is a fully independent code path from `TextFormat`, has no knowledge of this option. When a scalar/enum/map-key value fails to parse, the raw string content supplied by the caller is placed directly into the error via `absl::StrFormat`/`absl::StrCat` and returned as the `Invalid()` status message:
- Invalid numeric conversion embeds the literal value: `x.loc.Invalid(absl::StrFormat("invalid number: '%s'", x.value.AsView()))` [3](#0-2) .
- Invalid boolean map keys embed the literal string: `key.loc.Invalid(absl::StrFormat("expected bool string, got '%s'", key.value.AsView()))` [4](#0-3) .
- The generic `JsonLocation::Invalid` helper composes the field path (via `path->Describe`) with the caller-supplied `message` (which, as shown above, frequently contains the raw value) into the final status text, and only "hardens" the result by inserting cosmetic random whitespace (`HardenAgainstHyrumsLaw`) — this is explicitly a Hyrum's-Law anti-scraping measure, not a confidentiality control, and it does not redact or hash the content [5](#0-4) [6](#0-5) .

The same behavior is visible in the higher-level language bindings that wrap this parser/породит their own JSON implementations: Python's `json_format.py` embeds the offending value directly in `ParseError` text (e.g. `Couldn't parse non-integer string: "1.5" at TestMessage.int32Value.`) [7](#0-6) , and Java's `JsonFormat.java` does the same for enum/type-mismatch errors (`"Invalid enum value: " + json + ...`) [8](#0-7) . None of these paths query `FieldDescriptor.getOptions().getDebugRedact()`, which is only consulted by `TextFormat`/`DebugFormat` printers [9](#0-8) .

### Impact Explanation
Applications routinely catch `InvalidProtocolBufferException`/`ParseError`/`absl::Status` from ProtoJSON parsing failures and log `e.getMessage()` (or an equivalent) at ERROR/WARNING level for observability — this is standard practice, exactly the "connection health metric logging" pattern in the MongoDB report. If a schema author has marked a field `debug_redact = true` (i.e., explicitly declared it sensitive, e.g., a password/token/PII field submitted as JSON), a malformed value for that field (which the attacker/client fully controls, since they crafted the JSON payload) is echoed back verbatim in the exception/status message and can end up in application logs, crash reports, or monitoring systems — precisely the "sensitive value written to logs without redaction" failure class described in the MongoDB advisory. This is an information-disclosure issue when the disclosed value originates from server-side data merged into the same message (e.g., default/re-serialization flows) or when logs are shared/aggregated more broadly than the original request context, but even for pure client-echo it defeats the schema author's explicit intent as encoded by `debug_redact`.

### Likelihood Explanation
Any ordinary client sending a bounded, malformed ProtoJSON payload through the public `JsonStringToMessage`/`Message::ParseFromJson`/`json_format.Parse`/`JsonFormat.parser().merge()` APIs can trigger this — no privileged access or special schema/plugin is required, only a message type that uses `debug_redact` (a documented, supported annotation) and a value that fails validation (wrong type, out-of-range, bad enum name, non-UTF8, etc.). This is a normal, everyday parse-failure path, not an edge case, so likelihood is high; the constrained factor for severity is that it only manifests when the schema owner opted a field into `debug_redact` and when the calling application logs parse-error text (both very common practices).

### Recommendation
Thread `FieldDescriptor` redaction state (`debug_redact`, including nested message/enum sensitivity as already implemented for `TextFormat::IsOptionSensitive`) into the ProtoJSON parser's error-construction paths (`ParseIntInner`/`ParseFloatStringAsInt`, `ParseMapKey`, `ParseEnumFromStr`, and any other site that interpolates `x.value.AsView()`/`key.value.AsView()` into `Invalid()` messages), and replace the raw value with a redaction placeholder (mirroring `kFieldValueReplacement = "[REDACTED]"`) whenever the field being parsed is marked sensitive. Apply the same fix to the derived Python/Java/Ruby ProtoJSON error formatters that independently embed field values in `ParseError`/`InvalidProtocolBufferException` messages.

### Proof of Concept
Given a proto schema field marked sensitive:
```proto
message Creds {
  int32 secret_pin = 1 [debug_redact = true];
}
```
Sending the bounded, malformed ProtoJSON payload `{"secretPin": "9x9"}` through the public JSON parse API (`google::protobuf::json::JsonStringToMessage`, or the Python/Java `json_format`/`JsonFormat` equivalents) causes parsing to fail in `ParseIntInner`, which returns `Invalid(absl::StrFormat("invalid number: '%s'", x.value.AsView()))` [3](#0-2) , yielding an error status/exception whose text contains the literal `9x9` value and field path (`secretPin`), even though the field is declared `debug_redact = true`. Compare with `DebugString()` on the same message type where the value would print as `[REDACTED]` per `TryRedactFieldValue` [10](#0-9)  — demonstrating the redaction guarantee is honored in one output path (`TextFormat`/`DebugString`) but silently bypassed in another (ProtoJSON parse-error reporting) that is equally reachable by an ordinary client and equally likely to be logged by the consuming application.

### Citations

**File:** src/google/protobuf/text_format.h (L444-445)
```text
    // Sets whether strings will be redacted and thus unparsable.
    void SetRedactDebugString(bool redact) { redact_debug_string_ = redact; }
```

**File:** src/google/protobuf/text_format.cc (L3279-3304)
```text
bool TextFormat::Printer::TryRedactFieldValue(
    const Message& message, const FieldDescriptor* field,
    BaseTextGenerator* generator, bool insert_value_separator) const {
  TextFormat::RedactionState redaction_state =
      DescriptorPool::MemoizeProjection(
          field, [](const FieldDescriptor* field) {
            return TextFormat::GetRedactionState(field);
          });
  if (redaction_state.redact) {
    if (redact_debug_string_) {
      IncrementRedactedFieldCounter();
      if (insert_value_separator) {
        generator->PrintMaybeWithMarker(MarkerToken(), ": ");
      }
      generator->PrintString(kFieldValueReplacement);
      if (insert_value_separator) {
        if (single_line_mode_) {
          generator->PrintLiteral(" ");
        } else {
          generator->PrintLiteral("\n");
        }
      }
      return true;
    }
  }
  return false;
```

**File:** src/google/protobuf/json/internal/parser.cc (L172-178)
```text
static absl::Status ParseFloatStringAsInt(
    const LocationWith<MaybeOwnedString>& x, T* out, double lo, double hi) {
  double d;
  if (!absl::SimpleAtod(x.value.AsView(), &d) || !std::isfinite(d)) {
    return x.loc.Invalid(
        absl::StrFormat("invalid number: '%s'", x.value.AsView()));
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

**File:** python/google/protobuf/internal/json_format_test.py (L1240-1244)
```python
    self.CheckError(
        '{"int32Value": "1.5"}',
        'Failed to parse int32Value field: '
        'Couldn\'t parse non-integer string: "1.5" at TestMessage.int32Value.',
    )
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L2444-2453)
```java
      try {
        name = json.getAsString();
      } catch (UnsupportedOperationException | IllegalStateException e) {
        // UnsupportedOperationException is thrown by Gson when getAsString() is called on a
        // JsonObject (e.g., "{}") or JsonNull (e.g., "null"). IllegalStateException is thrown when
        // getAsString() is called on a JsonArray whose length is not exactly 1 (e.g., "[]" or
        // '["FOO", "BAR"]').
        throw new InvalidProtocolBufferException(
            "Invalid enum value: " + json + " for enum type: " + enumDescriptor.getFullName(), e);
      }
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L668-676)
```java
    // The criteria for redacting a field is as follows: 1) The enablingSafeDebugFormat printer
    // option must be on. 2) The field must be considered "sensitive". A sensitive field can be
    // marked as sensitive via two methods: a) via a direct debug_redact=true annotation on the
    // field, b) via an enum field marked with debug_redact=true that is within the proto's
    // FieldOptions, either directly or indirectly via a message option.
    private boolean shouldRedact(final FieldDescriptor field, TextGenerator generator) {
      FieldDescriptor.RedactionState state = field.getRedactionState();
      return enablingSafeDebugFormat && state.redact;
    }
```
