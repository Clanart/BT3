I found a solid, concrete analog reachable from a supported public API: `google::protobuf::json_internal::BinaryToJsonStream`, which is invoked when converting binary wire-format protos to ProtoJSON via the `TypeResolver`-based path.

The invariant that fails here mirrors the audit's core issue: **a function's return value that signals "did this operation actually complete correctly" is deliberately discarded, and the caller proceeds as if it succeeded.**

- `CodedInputStream::DecrementRecursionDepthAndPopLimit()` returns `ConsumedEntireMessage()` — i.e., whether the parse of the length-delimited field consumed *exactly* the declared number of bytes [1](#0-0) .
- In `UntypedMessage::DecodeDelimited`, this check is explicitly cast to `(void)` and ignored, with a `// TODO: Remove this suppression` comment marking it as a known gap rather than an intentional design decision [2](#0-1) .
- The rest of `DecodeDelimited` reads the string/bytes/submessage/packed-field payload and unconditionally calls `InsertField`, returning `absl::OkStatus()` regardless of whether the declared field length matched what was actually consumed [3](#0-2) .
- This is reached by the public `google::protobuf::util::BinaryToJsonString`/`BinaryToJsonStream` entry point through `UntypedMessage::ParseFromStream` → `Decode` → `DecodeDelimited` [4](#0-3) [5](#0-4) .

### Title
Discarded consumption-check return value in `UntypedMessage::DecodeDelimited` allows length/content mismatch to go undetected - (File: `src/google/protobuf/json/internal/untyped_message.cc`)

### Summary
`DecodeDelimited` reads a length-delimited field (string/bytes/submessage/packed repeated field) bounded by a pushed limit, then calls `stream.DecrementRecursionDepthAndPopLimit(limit)` — whose boolean return value indicates whether parsing actually consumed the entire declared length — but discards it with `(void)` instead of propagating a decode error [6](#0-5) .

### Finding Description
The check-return-value invariant broken in the Solidity report ("caller assumes an operation succeeded/consumed the stated amount without verifying it") transfers directly here. `PushLimit`/`ReadLengthAndPushLimit` establishes an expected byte-length for a length-delimited field [7](#0-6) . After the field's contents are parsed (string copy, nested message decode, or packed scalar loop), the code is supposed to confirm the parse landed exactly on the limit boundary via `ConsumedEntireMessage()` inside `DecrementRecursionDepthAndPopLimit` [1](#0-0) . Instead the result is thrown away with `(void)`, and the function unconditionally returns `absl::OkStatus()` [6](#0-5) . For the packed-scalar branch in particular, the loop condition is `while (stream.BytesUntilLimit() > 0)` [8](#0-7) ; if a crafted payload causes the loop to under- or over-shoot the declared limit in a way `PopLimit`'s internal bookkeeping tolerates without the `ConsumedEntireMessage` signal being checked, the mismatch is silently swallowed rather than surfaced as `MakeMalformedLengthDelimError`-class failure. The nested-message case (`Field::TYPE_MESSAGE`) is checked separately via `inner.status()` from the recursive `ParseFromStream`/`Decode` call [9](#0-8) , so this specific discarded check is the outer length-boundary confirmation, not the inner submessage's own field-level validity.

### Impact Explanation
This affects the `BinaryToJson` conversion path (`BinaryToJsonStream`/`BinaryToJsonString`), a supported public API for converting arbitrary attacker-supplied binary wire bytes to ProtoJSON using a `TypeResolver` [10](#0-9) . If the outer boundary isn't validated, a malformed/malicious binary payload where a length-delimited field's declared size and the size actually consumed by the inner decode diverge (per the wire semantics `ConsumedEntireMessage` is meant to catch) can be accepted as valid input instead of producing the intended `InvalidArgumentError`, silently producing output derived from mis-parsed/misaligned bytes rather than failing closed. This is a parser-correctness/integrity issue in a schema-agnostic, `TypeResolver`-driven decode path, not a memory-safety bug — severity should be assessed as Medium given it requires a specific inner-decode "silently stops early/late without erroring" condition to actually manifest.

### Likelihood Explanation
Reaching this code only requires calling the public `BinaryToJsonString`/`BinaryToJsonStream` API with attacker-controlled, syntactically-valid-but-crafted binary protobuf bytes and a trusted `TypeResolver`/schema — no privileged access needed. However, actually triggering an observable divergence requires an inner decode step (varint/fixed loop, string copy, or nested `Decode`) to terminate without erroring at a point other than exactly the pushed limit boundary, which is a narrower, payload-dependent condition; most malformed inputs will already fail earlier inside `ReadString`/`ReadVarint*`/`Skip` with an explicit `MakeUnexpectedEofError`.

### Recommendation
Do not discard the result of `DecrementRecursionDepthAndPopLimit`; propagate it as an error (e.g., return `MakeMalformedLengthDelimError()` or a new "length delimiter mismatch" status) when it returns `false`, mirroring how `WireFormatLite::ReadMessage`/`ReadGroup` in the same codebase already check this exact return value before treating a submessage parse as successful [11](#0-10) .

### Proof of Concept
A minimal local reproduction requires constructing, via `UntypedMessage::DecodeDelimited`'s packed-scalar branch, a length-delimited field whose declared varint length does not align with the actual boundary consumed by the inner `DecodeVarint`/`Decode32Bit`/`Decode64Bit` loop (e.g., a length that ends mid-varint but where the loop's `BytesUntilLimit() > 0` check still permits one more iteration to read past the intended boundary within slack still available in the outer buffer). Because the `(void)`-discarded `DecrementRecursionDepthAndPopLimit` call is the only place that would have detected the resulting `ConsumedEntireMessage() == false` condition, `DecodeDelimited` returns `absl::OkStatus()` unconditionally at line 531 instead of surfacing the mismatch. I was not able to execute this end-to-end in this environment (no build/test execution available here); a background Devin session with build tooling would be needed to compile a harness against `BinaryToJsonString` and confirm on-the-wire behavior for a specific crafted payload.

### Citations

**File:** src/google/protobuf/io/coded_stream.cc (L156-162)
```text
bool CodedInputStream::DecrementRecursionDepthAndPopLimit(Limit limit) {
  bool result = ConsumedEntireMessage();
  PopLimit(limit);
  ABSL_DCHECK_LT(recursion_budget_, recursion_limit_);
  ++recursion_budget_;
  return result;
}
```

**File:** src/google/protobuf/json/internal/untyped_message.cc (L461-532)
```text
absl::Status UntypedMessage::DecodeDelimited(io::CodedInputStream& stream,
                                             const ResolverPool::Field& field) {
  if (!stream.IncrementRecursionDepth()) {
    return MakeTooDeepError();
  }
  auto limit = stream.ReadLengthAndPushLimit();
  if (limit == 0) {
    return MakeUnexpectedEofError();
  }

  switch (field.proto().kind()) {
    case Field::TYPE_STRING:
    case Field::TYPE_BYTES: {
      std::string buf;
      if (!stream.ReadString(&buf, stream.BytesUntilLimit())) {
        return MakeUnexpectedEofError();
      }
      if (field.proto().kind() == Field::TYPE_STRING) {
        if (desc_->proto().syntax() == google::protobuf::SYNTAX_PROTO3 &&
            !utf8_range::IsStructurallyValid(buf)) {
          return MakeProto3Utf8Error();
        }
      }

      RETURN_IF_ERROR(InsertField(field, std::move(buf)));
      break;
    }
    case Field::TYPE_MESSAGE: {
      auto inner_desc = field.MessageType();
      RETURN_IF_ERROR(inner_desc.status());

      auto inner = ParseFromStream(*inner_desc, stream);
      RETURN_IF_ERROR(inner.status());
      RETURN_IF_ERROR(InsertField(field, std::move(*inner)));
      break;
    }
    default: {
      // This is definitely a packed field.
      while (stream.BytesUntilLimit() > 0) {
        switch (field.proto().kind()) {
          case Field::TYPE_BOOL:
          case Field::TYPE_INT32:
          case Field::TYPE_SINT32:
          case Field::TYPE_UINT32:
          case Field::TYPE_ENUM:
          case Field::TYPE_INT64:
          case Field::TYPE_SINT64:
          case Field::TYPE_UINT64:
            RETURN_IF_ERROR(DecodeVarint(stream, field));
            break;
          case Field::TYPE_FIXED64:
          case Field::TYPE_SFIXED64:
          case Field::TYPE_DOUBLE:
            RETURN_IF_ERROR(Decode64Bit(stream, field));
            break;
          case Field::TYPE_FIXED32:
          case Field::TYPE_SFIXED32:
          case Field::TYPE_FLOAT:
            RETURN_IF_ERROR(Decode32Bit(stream, field));
            break;
          default:
            return MakeInvalidLengthDelimType(field.proto().kind(),
                                              field.proto().number());
        }
      }
      break;
    }
  }
  // TODO: Remove this suppression.
  (void)stream.DecrementRecursionDepthAndPopLimit(limit);
  return absl::OkStatus();
}
```

**File:** src/google/protobuf/json/internal/unparser.cc (L894-931)
```text
absl::Status BinaryToJsonStream(google::protobuf::util::TypeResolver* resolver,
                                const std::string& type_url,
                                io::ZeroCopyInputStream* binary_input,
                                io::ZeroCopyOutputStream* json_output,
                                json_internal::WriterOptions options) {
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
```

**File:** src/google/protobuf/json/internal/untyped_message.h (L166-172)
```text
  // Tries to parse a proto with the given descriptor from an input stream.
  static absl::StatusOr<UntypedMessage> ParseFromStream(
      const ResolverPool::Message* desc, io::CodedInputStream& stream) {
    UntypedMessage msg(std::move(desc));
    RETURN_IF_ERROR(msg.Decode(stream));
    return std::move(msg);
  }
```

**File:** src/google/protobuf/wire_format_lite.h (L1203-1214)
```text
template <typename MessageType>
inline bool WireFormatLite::ReadMessage(io::CodedInputStream* input,
                                        MessageType* value) {
  int length;
  if (!input->ReadVarintSizeAsInt(&length)) return false;
  std::pair<io::CodedInputStream::Limit, int> p =
      input->IncrementRecursionDepthAndPushLimit(length);
  if (p.second < 0 || !value->MergePartialFromCodedStream(input)) return false;
  // Make sure that parsing stopped when the limit was hit, not at an endgroup
  // tag.
  return input->DecrementRecursionDepthAndPopLimit(p.first);
}
```
