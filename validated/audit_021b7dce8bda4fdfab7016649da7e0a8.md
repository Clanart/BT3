### Title
Sensitive/debug-redacted field values disclosed in plaintext via `ABSL_DLOG` when `PROTOBUF_DEBUG` is enabled - ([File: src/google/protobuf/json/internal/parser.cc], [File: src/google/protobuf/json/internal/unparser.cc])

### Summary
The Oxia advisory's failed invariant is: an authentication-relevant value (bearer token) that the application intends to keep confidential is written to a DEBUG-level log sink in cleartext, bypassing any expectation that sensitive values are redacted before logging. The Protobuf analog is in the JSON conversion helpers `JsonStreamToMessage`, `JsonToBinaryStream`, `BinaryToJsonStream`, and `MessageToJsonStream`, which — when the library is built/run with `PROTOBUF_DEBUG` enabled — emit the *raw serialized bytes* of client-supplied messages to `ABSL_DLOG(INFO)` (`"json2/input"`, `"json2/output"`), completely bypassing Protobuf's own `debug_redact` field-option redaction mechanism that exists specifically to prevent sensitive field values from appearing in debug/log output.

### Finding Description
Protobuf has a first-class mitigation for exactly this bug class: fields (or enum values) annotated `debug_redact = true` are redacted whenever a message is rendered via `TextFormat`/`DebugString()`. This is implemented in `TextFormat::Printer::TryRedactFieldValue` and `TextFormat::GetRedactionState`/`IsOptionSensitive` [1](#0-0) , and mirrored in Java's `Descriptors.FieldDescriptor.getRedactionState()` [2](#0-1) . `MessageToJsonStream` itself calls `message.DebugString()` for its debug trace, so it is correctly routed through this redaction path [3](#0-2) .

However, several sibling entry points in the same files log the **raw wire bytes** of the message directly, never constructing a `Message` object and therefore never passing through `debug_redact` filtering at all:
- `JsonStreamToMessage` logs `absl::CHexEscape(copy)` of the raw incoming JSON bytes before any field-level redaction decision is made [4](#0-3) .
- `JsonToBinaryStream` logs the hex-escaped raw JSON input and the hex-encoded raw binary output bytes [5](#0-4) .
- `BinaryToJsonStream` logs `absl::BytesToHexString(copy)` (raw input) and `absl::CHexEscape(out)` (raw output) [6](#0-5) .

These byte-level dumps operate on the encoded wire form, so `debug_redact`-annotated fields — which are only special-cased in the reflection-based `TextFormat` printer, not in the raw byte stream — are emitted verbatim in the log line if present in the payload.

### Impact Explanation
If a consuming application enables `PROTOBUF_DEBUG` (a compile-time debug flag) in an environment where JSON⇄binary conversion utilities (`google::protobuf::util::JsonToBinaryStream` / `BinaryToJsonStream`) handle client-controlled payloads containing fields the schema owner marked `debug_redact = true` (e.g., tokens, secrets, PII), those exact field values are written unredacted to the log stream, identical in kind to the Oxia token-leak advisory. This satisfies CWE-532 (Insertion of Sensitive Information into Log File) with disclosure impact only (no memory-safety/RCE impact), consistent with the analog's "VC:H" characterization in the original advisory.

### Likelihood Explanation
The `PROTOBUF_DEBUG` guard means this only fires when debug logging is explicitly enabled, exactly mirroring the original report's workaround/precondition ("Ensure DEBUG-level logging is never enabled in production"). In this repository, `PROTOBUF_DEBUG` is defined/derived in `src/google/protobuf/port.h`/`port_def.inc`; it is not proven from the available index whether it is off by default in release builds — this could not be fully confirmed within the indexed content, so the exact default-enablement state in shipped release configurations is uncertain and should be verified directly in a full checkout. Given that, likelihood is Medium: it requires (a) `PROTOBUF_DEBUG` builds, (b) use of the byte-oriented `JsonToBinaryStream`/`BinaryToJsonStream` conversion APIs on attacker-supplied payloads, and (c) presence of `debug_redact` fields in the schema — all plausible in a debug-instrumented gateway/proxy service that converts client JSON to binary Protobuf.

### Recommendation
Route the debug tee/logging paths in `JsonToBinaryStream`, `BinaryToJsonStream`, and `JsonStreamToMessage` through the same redaction-aware serialization used by `TextFormat`/`DebugString()` (or explicitly redact fields marked `debug_redact` before hex/CHexEscape dumping the raw bytes), rather than logging pre-redaction wire bytes directly. At minimum, document that `PROTOBUF_DEBUG` builds are unsafe for production traffic containing sensitive fields, matching the upstream advisory's stated workaround.

### Proof of Concept
Not independently executed (no filesystem/terminal access in this session). Conceptually: build protobuf with `PROTOBUF_DEBUG` defined, define a `.proto` message field with `[debug_redact = true]` (e.g., an `oauth_token` string field), call `google::protobuf::util::JsonToBinaryStream(resolver, type_url, json_input_containing_token, binary_output, options)` with a JSON payload where `oauth_token` is set to a real secret value, and observe the `ABSL_DLOG(INFO) << "json2/input: " << absl::CHexEscape(copy)` line contains the hex-encoded token in cleartext at `src/google/protobuf/json/internal/parser.cc:1423` [7](#0-6) , whereas the equivalent `message->DebugString()` call in `JsonStreamToMessage`/`MessageToJsonStream` would have redacted it per `TryRedactFieldValue` [8](#0-7) . This has not been run in this session; a Devin session with repository/terminal access would be needed to compile and confirm this end-to-end.

### Citations

**File:** src/google/protobuf/text_format.cc (L3265-3305)
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

**File:** java/core/src/main/java/com/google/protobuf/Descriptors.java (L2085-2111)
```java
    // Lazily calculates the redact state of the field, caching the result.
    RedactionState getRedactionState() {
      RedactionState state = redactionState;
      if (state == null) {
        // If the field is directly marked with debug_redact=true, then it is sensitive.
        synchronized (this) {
          state = redactionState;
          if (state == null) {
            FieldOptions options = getOptions();
            state = RedactionState.of(options.getDebugRedact());
            // Check if the FieldOptions contain any enums that are marked as debug_redact=true,
            // either directly or indirectly via a message option.
            for (Map.Entry<Descriptors.FieldDescriptor, Object> entry :
                options.getAllFields().entrySet()) {
              state =
                  RedactionState.combine(
                      state, isOptionSensitive(entry.getKey(), entry.getValue()));
              if (state.redact) {
                break;
              }
            }
            redactionState = state;
          }
        }
      }
      return state;
    }
```

**File:** src/google/protobuf/json/internal/unparser.cc (L878-887)
```text
absl::Status MessageToJsonStream(const Message& message,
                                 io::ZeroCopyOutputStream* json_output,
                                 json_internal::WriterOptions options) {
  if (PROTOBUF_DEBUG) {
    ABSL_DLOG(INFO) << "json2/input: " << message.DebugString();
  }
  JsonWriter writer(json_output, options);
  absl::Status s = WriteMessage<UnparseProto2Descriptor>(
      writer, message, *message.GetDescriptor(), /*is_top_level=*/true);
  if (PROTOBUF_DEBUG) ABSL_DLOG(INFO) << "json2/status: " << s;
```

**File:** src/google/protobuf/json/internal/unparser.cc (L908-946)
```text
  std::string copy;
  std::string out;
  absl::optional<io::ArrayInputStream> tee_input;
  absl::optional<io::StringOutputStream> tee_output;
  if (PROTOBUF_DEBUG) {
    const void* data;
    int len;
    while (binary_input->Next(&data, &len)) {
      copy.resize(copy.size() + len);
      std::memcpy(&copy[copy.size() - len], data, len);
    }
    tee_input.emplace(copy.data(), copy.size());
    tee_output.emplace(&out);
    ABSL_DLOG(INFO) << "json2/input: " << absl::BytesToHexString(copy);
  }

  ResolverPool pool(resolver);
  auto desc = pool.FindMessage(type_url);
  RETURN_IF_ERROR(desc.status());

  io::CodedInputStream stream(tee_input.has_value() ? &*tee_input
                                                    : binary_input);
  auto msg = UntypedMessage::ParseFromStream(*desc, stream);
  RETURN_IF_ERROR(msg.status());

  JsonWriter writer(tee_output.has_value() ? &*tee_output : json_output,
                    options);
  absl::Status s = WriteMessage<UnparseProto3Type>(
      writer, *msg, UnparseProto3Type::GetDesc(*msg),
      /*is_top_level=*/true);
  if (PROTOBUF_DEBUG) ABSL_DLOG(INFO) << "json2/status: " << s;
  RETURN_IF_ERROR(s);

  if (PROTOBUF_DEBUG) {
    tee_output.reset();  // Flush the output stream.
    io::zc_sink_internal::ZeroCopyStreamByteSink(json_output)
        .Append(out.data(), out.size());
    ABSL_DLOG(INFO) << "json2/output: " << absl::CHexEscape(out);
  }
```

**File:** src/google/protobuf/json/internal/parser.cc (L1396-1453)
```text
absl::Status JsonToBinaryStream(google::protobuf::util::TypeResolver* resolver,
                                const std::string& type_url,
                                io::ZeroCopyInputStream* json_input,
                                io::ZeroCopyOutputStream* binary_output,
                                json_internal::ParseOptions options) {
  // NOTE: Most of the contortions in this function are to allow for capture of
  // input and output of the parser in ABSL_DLOG mode. Destruction order is very
  // critical in this function, because io::ZeroCopy*Stream types usually only
  // flush on destruction.

  // For ABSL_DLOG, we would like to print out the input and output, which
  // requires buffering both instead of doing "zero copy". This block, and the
  // one at the end of the function, set up and tear down interception of the
  // input and output streams.
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

  // This scope forces the CodedOutputStream inside of `msg` to flush before we
  // possibly handle logging the binary protobuf output.
  absl::Status s;
  {
    MessagePath path(type_url);
    JsonLexer lex(tee_input.has_value() ? &*tee_input : json_input, options,
                  &path);
    Msg<ParseProto3Type> msg(tee_output.has_value() ? &*tee_output
                                                    : binary_output);

    ResolverPool pool(resolver);
    auto desc = pool.FindMessage(type_url);
    RETURN_IF_ERROR(desc.status());

    s = ParseMessage<ParseProto3Type>(lex, **desc, msg, /*any_reparse=*/false);
    if (s.ok() && !lex.AtEof()) {
      s = absl::InvalidArgumentError(
          "extraneous characters after end of JSON object");
    }
  }

  if (PROTOBUF_DEBUG) {
    tee_output.reset();  // Flush the output stream.
    io::zc_sink_internal::ZeroCopyStreamByteSink(binary_output)
        .Append(out.data(), out.size());
    ABSL_DLOG(INFO) << "json2/status: " << s;
    ABSL_DLOG(INFO) << "json2/output: " << absl::BytesToHexString(out);
  }
```
