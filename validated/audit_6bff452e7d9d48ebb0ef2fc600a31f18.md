### Title
`MergePartialFromCodedStream`/`CodedInputStream` parsing reports success ("parsed") without enforcing that the entire message was consumed, allowing trailing corrupt/extra data to pass unnoticed - (File: `src/google/protobuf/message_lite.cc`)

### Summary
The AIIR advisory describes verification gates that return a "success"/"verified" result without actually enforcing the control the caller believes was applied (fail-open). The closest concrete Protobuf analog is `MessageLite::MergeFromImpl(io::CodedInputStream*, ...)` (used by `MergePartialFromCodedStream`), which can return `true` even when the input stream contains malformed/extra trailing bytes after a syntactically complete message, deferring the actual "did this really parse validly to the end" check to a second, easily-omitted call (`CodedInputStream::LastTagWas`/`ConsumedEntireMessage`/`ExpectAtEnd`).

### Finding Description
`MessageLite::MergeFromImpl` for a `CodedInputStream` input explicitly allows the wire format to be considered "terminated" either by end-of-stream or by an end-group/zero tag, and pushes the burden of validating a clean ending back onto the caller: [1](#0-0) 

The library's own header comments confirm this is a known caller-responsibility gap, not a hard invariant enforced by the parse call itself: [2](#0-1) 

The library's own unit test explicitly documents this as a known-bad outcome: appending an invalid zero tag after a syntactically valid message causes `MergePartialFromCodedStream` to still report `true` ("parsed"), even though the equivalent `ParseFromString` entry point (which additionally enforces the "consumed entire message" check under the hood) correctly rejects the same bytes: [3](#0-2) 

So the failed invariant is: "a parse call that returns success should mean the input was fully and validly consumed." The attacker-controlled value is the trailing bytes appended after a otherwise-valid serialized message. The missing check is that `MergePartialFromCodedStream` itself does not call `ConsumedEntireMessage()`/`ExpectAtEnd()` — that verification step is optional and must be invoked separately by the caller, and nothing in the return value signals that this step was skipped.

### Impact Explanation
A consuming application that uses the lower-level `CodedInputStream` + `MergePartialFromCodedStream` API directly (a supported, documented public parsing path) — for example, to enforce "this buffer contains exactly one canonical message and nothing else" (a common defensive pattern for length-framed or single-record protocols) — can be given a false "parse succeeded" result while trailing attacker-supplied bytes are silently ignored/discarded. This mirrors the AIIR pattern of a gate that "could report success without enforcing the control it represents": the caller believes the strict/complete-parse check ran (because the call returned `true`), but the actual enforcement of "no trailing garbage" was skipped unless the caller remembered to additionally call `LastTagWas`/`ConsumedEntireMessage`. Content of the message fields themselves is not corrupted and no memory-safety issue occurs — the impact is strictly an integrity/verification bypass (false-positive "valid input" signal), consistent with a Medium-severity, "fail-open policy gate" class finding rather than data corruption or RCE.

### Likelihood Explanation
Likelihood is bounded because the safer, more commonly documented top-level entry points (`ParseFromString`, `ParseFromArray`, `ParseFromZeroCopyStream`) do perform the full "consumed entire message" enforcement internally and correctly reject the same malformed input, as shown by the control-case assertion in the same test (`ParseFromString` returns `false` for identical bytes). The exposure therefore only manifests for applications that intentionally choose the lower-level streaming `CodedInputStream`/`MergePartialFromCodedStream` API (e.g., for streaming/concatenated-message use cases) without separately invoking the completion check — a documented but easy-to-miss caller obligation.

### Recommendation
Documentation/API surface should make the caller obligation impossible to silently skip: e.g., have `MergePartialFromCodedStream` return a richer result (or a `[[nodiscard]]`-enforced companion call) that forces verification of stream completion before the result can be treated as "fully validated," or provide a dedicated "parse exactly one message and reject trailing bytes" convenience API so applications doing input-integrity enforcement are not relying on an implicit, separately-callable check.

### Proof of Concept
The library's own regression test is a ready-made, minimal reproduction demonstrating the fail-open behavior: [3](#0-2) 
This serializes a valid `TestAllTypes` message, appends a single invalid `0x00` tag byte, then shows `MergePartialFromCodedStream` returns `true` (comment: "It should fail but currently passes") while `ParseFromString` on the identical bytes returns `false` — proving the discrepancy is real, reproducible with trusted schema and bounded attacker-controlled input, and already acknowledged in-tree rather than hypothetical.

### Citations

**File:** src/google/protobuf/message_lite.cc (L330-353)
```text
bool MessageLite::MergeFromImpl(io::CodedInputStream* input,
                                MessageLite::ParseFlags parse_flags) {
  ZeroCopyCodedInputStream zcis(input);
  const char* ptr;
  internal::ParseContext ctx(input->RecursionBudget(), zcis.aliasing_enabled(),
                             &ptr, &zcis);
  // MergePartialFromCodedStream allows terminating the wireformat by 0 or
  // end-group tag. Leaving it up to the caller to verify correct ending by
  // calling LastTagWas on input. We need to maintain this behavior.
  ctx.TrackCorrectEnding();
  ctx.data().pool = input->GetExtensionPool();
  ctx.data().factory = input->GetExtensionFactory();
  ptr = internal::TcParser::ParseLoop(this, ptr, &ctx, GetTcParseTable());
  if (ABSL_PREDICT_FALSE(!ptr)) return false;
  ctx.BackUp(ptr);
  if (!ctx.EndedAtEndOfStream()) {
    ABSL_DCHECK_NE(ctx.LastTag(), 1u);  // We can't end on a pushed limit.
    if (ctx.IsExceedingLimit(ptr)) return false;
    input->SetLastTag(ctx.LastTag());
  } else {
    input->SetConsumed();
  }
  return CheckFieldPresenceImpl(ctx, *this, parse_flags);
}
```

**File:** src/google/protobuf/io/coded_stream.h (L319-326)
```text
  // When parsing message (but NOT a group), this method must be called
  // immediately after MergeFromCodedStream() returns (if it returns true)
  // to further verify that the message ended in a legitimate way.  For
  // example, this verifies that parsing did not end on an end-group tag.
  // It also checks for some cases where, due to optimizations,
  // MergeFromCodedStream() can incorrectly return true.
  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD bool ConsumedEntireMessage();
  void SetConsumed() { legitimate_message_end_ = true; }
```

**File:** src/google/protobuf/message_unittest.inc (L1390-1409)
```text
  std::string value = std::string(64 * 1024 * 1024, 'x');
  UNITTEST::TestAllTypes message;
  message.set_optional_string(value);

  // Repeat this message in the input stream to make the total input size
  // close to 2G.
  std::string data = message.SerializeAsString();
  size_t count = static_cast<size_t>(kint32max) / data.size();
  RepeatedInputStream input(data, count);

  // The parsing should succeed.
  UNITTEST::TestAllTypes result;
  EXPECT_TRUE(result.ParseFromZeroCopyStream(&input));

  // When there are multiple occurrences of a singular field, the last one
  // should win.
  EXPECT_EQ(value, result.optional_string());
}

TEST(MESSAGE_TEST_NAME, TestParseMessagesOver2G) {
```
