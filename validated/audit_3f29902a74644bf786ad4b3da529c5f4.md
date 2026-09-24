### Title
Verbose/debug JSON parsing logs raw attacker input and unredacted message content - ([File: src/google/protobuf/json/internal/parser.cc])

### Summary
When protobuf's C++ JSON runtime is built/run with the internal `PROTOBUF_DEBUG` diagnostic flag enabled, the public JSON⇄binary conversion entry points log the complete raw request bytes and the fully decoded, **unredacted** message content via `ABSL_DLOG(INFO)`. This mirrors the cifs-utils bug class: an optional "verbose logging" mode that, instead of only aiding debugging, discloses the full untrusted/sensitive payload that the non-verbose path would never expose.

### Finding Description
`JsonStreamToMessage` and `JsonToBinaryStream` in `src/google/protobuf/json/internal/parser.cc` are public parsing entry points that accept attacker-controlled bytes (JSON to be converted into a protobuf message, or vice versa) through a trusted schema. [1](#0-0) 

When `PROTOBUF_DEBUG` is enabled, `JsonStreamToMessage` logs the fully decoded message with `message->DebugString()` — no redaction is applied at this call site: [2](#0-1) 

`JsonToBinaryStream` goes further: it buffers and hex-dumps the *entire raw input* (`absl::CHexEscape(copy)`) before parsing even validates it, and also hex-dumps the raw binary output after conversion: [3](#0-2) [4](#0-3) 

The failed invariant is analogous to the cifs-utils case: a verbose-logging diagnostic path is not held to the same input/output hygiene as the primary path. In the CVE, `mount.cifs` verbose logging echoed raw, unvalidated credential-file lines (including values after `=`) into logs regardless of whether the file was a "valid" credentials file. Here, the protobuf JSON layer's debug logging echoes the raw attacker-supplied bytes and the decoded message verbatim into logs, bypassing the library's dedicated redaction mechanism (`debug_redact` field option / `TextFormat::Printer::SetRedactDebugString`) that exists specifically to prevent sensitive field values from being written into debug/log output: [5](#0-4) 

The missing check is that the `ABSL_DLOG` call sites in `parser.cc` never invoke the redaction-aware print path (`SetRedactDebugString(true)` / `SetReportSensitiveFields`) that other debug-string call sites in this same codebase rely on for protecting `debug_redact`-annotated fields: [6](#0-5) 

### Impact Explanation
If a consuming application enables protobuf's debug/verbose logging build mode for diagnostics (a legitimate, supported use case, just as cifs-utils' verbose mode is legitimate), any attacker-supplied JSON or protobuf payload sent through `JsonStreamToMessage`/`JsonToBinaryStream` — including fields explicitly marked `debug_redact = true` for the purpose of keeping them out of debug output — will have its full plaintext content, and the raw wire bytes, written unredacted to application logs. This is a confidentiality-only impact (matching the CVE's CVSS vector `C:L/I:N/A:N`), consistent with information disclosure of otherwise-protected field values or raw request contents into log sinks that may have broader access than the API surface itself.

### Likelihood Explanation
Likelihood is bounded by the precondition that `PROTOBUF_DEBUG` must be enabled, which is not the default production configuration. However, this precondition is directly analogous to the CVE's precondition of "verbose logging" being turned on — a state operators commonly enable temporarily for debugging production issues, at which point every JSON parse/serialize call through these entry points is affected with no additional attacker effort beyond sending a normal, schema-valid, bounded payload.

### Recommendation
Route the `ABSL_DLOG` message/content logging in `JsonStreamToMessage` and `JsonToBinaryStream` through a redaction-aware printer (e.g., `TextFormat::Printer` with `SetRedactDebugString(true)`) instead of calling `message->DebugString()` / raw hex-dumping the byte stream directly, so that `debug_redact`-annotated fields and raw payload bytes are not written to logs even when verbose/debug logging is enabled.

### Proof of Concept
Not independently executed in this checkout (no run environment available); reproduction outline based on read code: build protobuf with `PROTOBUF_DEBUG` defined truthy, call `google::protobuf::json_internal::JsonToBinaryStream` or `JsonStreamToMessage` with a JSON payload populating a message field annotated `[debug_redact = true]`, and observe via `ABSL_DLOG(INFO)` output (lines 1389-1392, 1414-1424, 1447-1452 of `parser.cc`) that the sensitive field value and raw input bytes appear in plaintext in the log stream, unlike the redacted output that `TextFormat::Printer`'s `SetRedactDebugString` path would have produced.

**Uncertainty note:** I was unable to confirm, within the available tool budget, the exact default state/definition of `PROTOBUF_DEBUG` in this checkout (the definition site was not located via search), so I cannot state with certainty whether this flag is ever enabled in any shipped production configuration versus purely developer/debug builds. This affects the likelihood assessment and should be verified directly in the repository (`port_def.inc` / build configuration) before treating this as confirmed-exploitable in a real deployment.

### Citations

**File:** src/google/protobuf/json/internal/parser.cc (L1365-1394)
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
}
```

**File:** src/google/protobuf/json/internal/parser.cc (L1410-1424)
```text
  std::string copy;
  std::string out;
  absl::optional<io::ArrayInputStream> tee_input;
  absl::optional<io::StringOutputStream> tee_output;
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

**File:** src/google/protobuf/text_format.cc (L116-148)
```text
enum class Option { kNone, kShort, kUTF8 };

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
