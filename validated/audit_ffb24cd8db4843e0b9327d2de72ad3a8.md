Based on my research, the most defensible analog is in Protobuf's JSON conversion debug-logging path, which bypasses the redaction protections that Protobuf otherwise built specifically to prevent this exact bug class.

### Title
Sensitive JSON/binary payload bytes are logged in cleartext, bypassing field redaction, when `PROTOBUF_ENABLE_DEBUG_LOGGING_MAY_LEAK_PII` debug logging is enabled - ([File: src/google/protobuf/json/internal/parser.cc])

### Summary
`JsonStreamToMessage` and `JsonToBinaryStream` in `src/google/protobuf/json/internal/parser.cc` unconditionally log the *raw* input JSON bytes and *raw* serialized output bytes via `ABSL_DLOG(INFO)` whenever the compile-time flag `PROTOBUF_DEBUG` is set (which is derived from `PROTOBUF_ENABLE_DEBUG_LOGGING_MAY_LEAK_PII`) [1](#0-0) [2](#0-1) [3](#0-2) . This is the same underlying condition as the OpenNMS/Jetty report: "when logging level is set to debug, sensitive data ends up in log files." Unlike the field-aware `TextFormat`/`DebugFormat` printer, which redacts fields annotated `debug_redact = true` before printing [4](#0-3) [5](#0-4) , these debug-tee log statements dump the raw hex bytes of the wire-level JSON/protobuf payload, completely outside the redaction machinery.

### Finding Description
Protobuf has an explicit, well-tested hardening mechanism (`TextFormat::Printer::TryRedactFieldValue`, `RedactionState`, `debug_redact` field option) whose entire purpose is to prevent sensitive field values from being written into debug logs/`DebugString()` output [6](#0-5) [7](#0-6) . `Message::DebugString()`/`ShortDebugString()`/`Utf8DebugString()`/`AbslStringify` all route through `StringifyMessage`, which sets `SetRedactDebugString(true)` so annotated sensitive fields are printed as `[REDACTED]` [8](#0-7) .

However, the JSON conversion entry points (`JsonStreamToMessage`, used by the public `util::JsonStringToMessage`/`JsonToBinaryStream` APIs) contain an independent debug-logging path gated only by the `PROTOBUF_DEBUG` compile flag, defined as: [9](#0-8) 
This path buffers and logs:
- The complete raw input JSON bytes, hex-escaped, before any field-level parsing/redaction occurs: `ABSL_DLOG(INFO) << "json2/input: " << absl::CHexEscape(copy);` [10](#0-9) 
- The fully-parsed message via `message->DebugString()` for `JsonStreamToMessage` (this one *is* redacted since it uses the field-aware printer) [1](#0-0) 
- The complete serialized *binary* output bytes as a raw hex string for `JsonToBinaryStream`, with no field-awareness or redaction at all: `ABSL_DLOG(INFO) << "json2/output: " << absl::BytesToHexString(out);` [3](#0-2) 

Because these log statements operate on raw bytes (the tee'd `copy`/`out` buffers) rather than through `TextFormat::Printer`/`GetRedactionState`, any field marked `debug_redact = true` in the trusted schema (e.g., a password, token, or PII field) is fully present in cleartext in these two log lines even though the schema explicitly asked for it to be redacted. This directly defeats the invariant the rest of Protobuf enforces: "fields annotated `debug_redact=true` must never appear in cleartext in debug output."

### Impact Explanation
An ordinary client sending a bounded, well-formed JSON payload (or triggering `JsonToBinaryStream`) through the public JSON<->binary conversion API is enough to have their full JSON/binary payload — including any values in fields the schema owner deliberately marked sensitive — written to the process's debug logs in cleartext when the consuming application has debug logging enabled for this component. This matches CWE-532 (Insertion of Sensitive Information into Log File) and the CVSS vector of the source advisory (confidentiality impact only, low complexity, network-reachable, no special privileges), since it requires no crash, no memory corruption, and is a straightforward confidentiality violation for whichever consuming service enables the flag.

### Likelihood Explanation
The macro name itself, `PROTOBUF_ENABLE_DEBUG_LOGGING_MAY_LEAK_PII`, is a deliberate, in-code acknowledgment of exactly this risk, and it defaults to `false` (log statements are compiled out via `ABSL_DLOG`/`if (PROTOBUF_DEBUG)` in ordinary release builds) [9](#0-8) . Likelihood is therefore Medium: it requires the consuming application/build to opt into this diagnostic flag — directly mirroring the OpenNMS advisory's precondition of "if the logging level is set to debug." Once enabled, every JSON conversion call is unconditionally affected; there is no additional attacker action needed beyond sending an ordinary, otherwise-valid payload.

### Recommendation
Route the `json2/input` and `json2/output` debug log statements through the same field-aware, redaction-capable printer (`TextFormat::Printer` with `SetRedactDebugString(true)`/`DebugFormat`) instead of dumping raw hex/bytes of the wire payload, or explicitly document/enforce that this flag must never be enabled with schemas containing `debug_redact` fields in production. At minimum, gate the raw-byte dump behind an additional, more prominent guard (e.g., require both the build flag and an explicit runtime opt-in) so schema-level redaction guarantees are not silently defeated by unrelated debug tooling.

### Proof of Concept
Conceptual reproduction (schema and API are trusted/well-formed; only the payload content is attacker-controlled):
1. Build protobuf with `-DPROTOBUF_ENABLE_DEBUG_LOGGING_MAY_LEAK_PII=1` (as some debugging/staging configurations might).
2. Define a trusted message schema with a field `string password = 1 [debug_redact = true];`.
3. An ordinary client calls the public API `google::protobuf::json::JsonStringToMessage(R"({"password":"S3cr3t!"})", &msg)`, which internally calls `JsonStreamToMessage` [11](#0-10) .
4. `ABSL_DLOG(INFO) << "json2/input: " << absl::CHexEscape(copy);` logs the hex-encoded raw JSON text `{"password":"S3cr3t!"}` in full — the `debug_redact` annotation has no effect on this line because it never passes through `TextFormat::Printer`.
5. Similarly, calling `JsonToBinaryStream` on the same JSON logs the fully serialized binary bytes (which encode `S3cr3t!` in cleartext protobuf wire format) via `absl::BytesToHexString(out)` [3](#0-2) , again bypassing redaction.

I did not find evidence of a runtime test asserting that these two specific log lines respect `debug_redact`; the only redaction-tested log path is `message->DebugString()` on line 1391, and even that only covers the fully-parsed-message log line, not the raw input/output tee logs.

### Citations

**File:** src/google/protobuf/json/internal/parser.cc (L1365-1393)
```text
absl::Status JsonStreamToMessage(io::ZeroCopyInputStream* input,
                                 Message* message,
                                 json_internal::ParseOptions options) {
  // Pre-existing special case behavior: if a Struct, List, or Value WKT is
  // provided, they do fully get cleared eagerly. All other types have some
  // limited merge behavior instead.
  MessageType type = ClassifyMessage(message->GetDescriptor()->full_name());
  if (type == MessageType::kStruct || type == MessageType::kList ||
      type == MessageType::kValue) {
    message->Clear();
  }

  MessagePath path(message->GetDescriptor()->full_name());
  JsonLexer lex(input, options, &path);

  ParseProto2Descriptor::Msg msg(message);
  absl::Status s =
      ParseMessage<ParseProto2Descriptor>(lex, *message->GetDescriptor(), msg,
                                          /*any_reparse=*/false);
  if (s.ok() && !lex.AtEof()) {
    s = absl::InvalidArgumentError(
        "extraneous characters after end of JSON object");
  }

  if (PROTOBUF_DEBUG) {
    ABSL_DLOG(INFO) << "json2/status: " << s;
    ABSL_DLOG(INFO) << "json2/output: " << message->DebugString();
  }
  return s;
```

**File:** src/google/protobuf/json/internal/parser.cc (L1414-1424)
```text
  if (PROTOBUF_DEBUG) {
    const void* data;
    int len;
    while (json_input->Next(&data, &len)) {
      copy.resize(copy.size() + len);
      std::memcpy(&copy[copy.size() - len], data, len);
    }
    tee_input.emplace(copy.data(), copy.size());
    tee_output.emplace(&out);
    ABSL_DLOG(INFO) << "json2/input: " << absl::CHexEscape(copy);
  }
```

**File:** src/google/protobuf/json/internal/parser.cc (L1447-1453)
```text
  if (PROTOBUF_DEBUG) {
    tee_output.reset();  // Flush the output stream.
    io::zc_sink_internal::ZeroCopyStreamByteSink(binary_output)
        .Append(out.data(), out.size());
    ABSL_DLOG(INFO) << "json2/status: " << s;
    ABSL_DLOG(INFO) << "json2/output: " << absl::BytesToHexString(out);
  }
```

**File:** src/google/protobuf/text_format.cc (L118-169)
```text
std::string StringifyMessage(const Message& message, Option option,
                             FieldReporterLevel reporter_level) {
  // Indicate all scoped reflection calls are from DebugString function.
  ScopedReflectionMode scope(ReflectionMode::kDebugString);

  TextFormat::Printer printer;
  internal::FieldReporterLevel reporter = reporter_level;
  switch (option) {
    case Option::kShort:
      printer.SetSingleLineMode(true);
      break;
    case Option::kUTF8:
      printer.SetUseUtf8StringEscaping(true);
      break;
    case Option::kNone:
      break;
  }
  printer.SetExpandAny(true);
  printer.SetRedactDebugString(true);
  printer.SetRandomizeDebugString(true);
  printer.SetReportSensitiveFields(reporter);
  std::string result;
  // TODO: Remove this suppression.
  (void)printer.PrintToString(message, &result);

  if (option == Option::kShort) {
    TrimTrailingSpace(result);
  }

  return result;
}

PROTOBUF_EXPORT std::string StringifyMessage(const Message& message) {
  return StringifyMessage(message, Option::kNone,
                          FieldReporterLevel::kAbslStringify);
}
}  // namespace internal

std::string Message::DebugString() const {
  return internal::StringifyMessage(*this, internal::Option::kNone,
                                    FieldReporterLevel::kDebugString);
}

std::string Message::ShortDebugString() const {
  return internal::StringifyMessage(*this, internal::Option::kShort,
                                    FieldReporterLevel::kShortDebugString);
}

std::string Message::Utf8DebugString() const {
  return internal::StringifyMessage(*this, internal::Option::kUTF8,
                                    FieldReporterLevel::kUtf8DebugString);
}
```

**File:** src/google/protobuf/text_format.cc (L3265-3278)
```text
TextFormat::RedactionState TextFormat::GetRedactionState(
    const FieldDescriptor* field) {
  auto options = field->options();
  auto state = TextFormat::RedactionState{options.debug_redact(), false};
  std::vector<const FieldDescriptor*> field_options;
  const Reflection* reflection = options.GetReflection();
  reflection->ListFields(options, &field_options);
  for (const FieldDescriptor* option : field_options) {
    auto result = TextFormat::IsOptionSensitive(options, reflection, option);
    state = TextFormat::RedactionState{state.redact || result.redact,
                                       state.report || result.report};
  }
  return state;
}
```

**File:** src/google/protobuf/text_format.cc (L3279-3305)
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
}
```

**File:** src/google/protobuf/redaction_metric_test.cc (L28-36)
```text
TEST(TextFormatParsingMetricsTest, MetricsTest) {
  std::string value_replacement = "[REDACTED]";
  proto2_unittest::RedactedFields proto;
  proto.set_optional_redacted_string("foo");
  int64_t before = internal::GetRedactedFieldCount();
  EXPECT_THAT(absl::StrCat(proto), HasSubstr(value_replacement));
  int64_t after = internal::GetRedactedFieldCount();
  EXPECT_EQ(after, before + 1);
}
```

**File:** src/google/protobuf/port_def.inc (L705-710)
```text
#if defined(PROTOBUF_ENABLE_DEBUG_LOGGING_MAY_LEAK_PII) && \
    PROTOBUF_ENABLE_DEBUG_LOGGING_MAY_LEAK_PII
#define PROTOBUF_DEBUG true
#else
#define PROTOBUF_DEBUG false
#endif
```
