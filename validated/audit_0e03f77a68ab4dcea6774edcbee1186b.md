### Title
Asymmetric recursion-depth/limit bookkeeping on error paths in `WireFormatLite::ReadGroup`/`ReadMessage` corrupts a reused `CodedInputStream`'s persistent parser state - (File: `src/google/protobuf/wire_format_lite.h`)

### Summary
The Fei report's root cause is a **paired accounting invariant** (`stakedBalance`/`totalStaked`) that is incremented on one operation (deposit) but not symmetrically decremented on the corresponding operation (withdraw) when an early-exit/error path is taken, corrupting shared, persistent state and causing later legitimate operations to fail (`"Redeemable underflow"`). The closest structural analog in this Protobuf checkout is `WireFormatLite::ReadGroup`/`ReadMessage`, which call `CodedInputStream::IncrementRecursionDepth()` / `IncrementRecursionDepthAndPushLimit()` (analogous to the deposit-side increment) but, on the failure branches, return `false` **without** calling the matching `DecrementRecursionDepth()` / `DecrementRecursionDepthAndPopLimit()` (analogous to the missing withdraw-side decrement). `CodedInputStream::recursion_budget_` and its internal limit stack are members of a single `CodedInputStream` object, which — like `totalStaked` — is often reused/shared across multiple sequential, independent parse operations (e.g., parsing a stream of length-delimited messages with one `CodedInputStream`).

### Finding Description
`CodedInputStream` tracks two paired counters that must be pushed/popped or incremented/decremented in lockstep, exactly like the pool's `stakedBalance`/`totalStaked` pair: [1](#0-0) 

`IncrementRecursionDepthAndPushLimit` performs the "deposit" side unconditionally: [2](#0-1) 

`ReadMessage`/`ReadGroup` call this increment/push, but on both failure branches (`p.second < 0` recursion-budget exhausted, or `MergePartialFromCodedStream` returning `false`) they `return false` immediately, **skipping** `DecrementRecursionDepthAndPopLimit`: [3](#0-2) 

Compare with the corresponding "withdraw" step that is supposed to restore state: [4](#0-3) 

The failure to call this on error paths leaves:
1. `recursion_budget_` permanently decremented by 1 for the lifetime of the `CodedInputStream` object.
2. The limit stack (`current_limit_`) still constrained to the failed submessage's byte range, never popped back to the outer limit.

By contrast, the newer table-driven `ParseContext`/`TcParser` path (used by most current generated `_InternalParse` code) is careful to always restore `depth_` unconditionally after the recursive call regardless of success/failure: [5](#0-4) 

This shows the project is aware that depth/limit restoration must be unconditional — but the legacy `WireFormatLite::ReadGroup`/`ReadMessage` reflection-based path (still compiled, exported, and reachable from any generated/custom `MergePartialFromCodedStream`/`_InternalParse` override, or via `Message::MergeFrom(CodedInputStream*)` on the reflection-based fallback runtime) does not have this guarantee.

**Failed invariant:** recursion-budget decrements and limit pushes must always be paired with a matching restore, independent of success/failure, when the underlying `CodedInputStream` outlives a single failed sub-parse.
**Attacker-controlled value:** the bytes of one submessage in a stream of otherwise independently-parsed messages sharing one `CodedInputStream` (e.g., a delimited multi-message stream a service reads message-by-message from a socket/file with one stream object).
**Missing check:** no `Decrement.../PopLimit` on the early-return error paths in `ReadGroup`/`ReadMessage`.
**Impact:** persistent corruption of parser state that outlives the single malformed input and affects subsequent, independent, well-formed messages parsed with the same stream object — an availability/integrity issue analogous to the locked/incorrect `totalStaked` value blocking legitimate withdrawals.

### Impact Explanation
If application code creates one `CodedInputStream` and repeatedly calls it to parse a sequence of length-delimited messages (a common, documented streaming pattern, since `CodedInputStream` explicitly supports `PushLimit`/`PopLimit` and `ReadVarintSizeAsInt` for this purpose), an attacker who controls one message in the sequence can supply bytes that cause `ReadMessage`/`ReadGroup` for some field to fail (e.g., malformed nested field). This leaves the limit stack and recursion budget in the wrong state for **all subsequent parses on that same stream object**, causing legitimate, well-formed later messages to be truncated (limit still pointing at old boundary) or spuriously rejected as exceeding the recursion limit (`recursion_budget_` never restored, eventually reaching -1 for deeply nested but legitimate messages). This is a self-inflicted denial-of-service / integrity failure on trusted downstream data, not memory corruption. Severity should be scoped as **Medium** — it degrades availability/correctness of a shared parser object, requires a specific streaming-reuse pattern, and does not itself corrupt memory or leak data across trust boundaries.

### Likelihood Explanation
Likelihood is limited by whether the affected legacy WireFormatLite recursive-message/group reading path is exercised: it is present in the "reflection lite" fallback and is directly callable by hand-written or generated code that still relies on `MergePartialFromCodedStream`/`CodedInputStream` rather than the modern `ParseContext`/`TcParser` fast path used by default `protoc`-generated `_InternalParse`. It requires the application to reuse a single `CodedInputStream` object across multiple independent parses (a supported but not universally used pattern) and for one of those parses to legitimately fail partway through a nested message/group.

### Recommendation
Ensure `WireFormatLite::ReadGroup` and `ReadMessage` always restore the recursion budget and pop the pushed limit on every exit path, mirroring the unconditional restore pattern already used in `ParseContext::ParseLengthDelimitedInlined`/`ParseGroupInlined` (e.g., wrap the push/increment in an RAII guard or explicitly call `Decrement.../PopLimit` before returning `false` on both failure branches).

### Proof of Concept
Not executed (index-based analysis only; no build/runtime environment available in this session). A minimal local reproduction would:
1. Construct a single `google::protobuf::io::CodedInputStream input(...)` over a byte buffer containing two independent, length-delimited `TestMessage` frames back-to-back, where frame 1 contains a submessage field whose nested `MergePartialFromCodedStream` is made to fail (e.g., a corrupt varint tag inside it) and frame 2 is a fully valid, deeply (but within-limit) nested message.
2. Call `WireFormatLite::ReadMessage(&input, &msg1)` for frame 1 and assert it returns `false`.
3. Call `WireFormatLite::ReadMessage(&input, &msg2)` for frame 2 using the *same* `input` object and assert/observe that it now spuriously fails or reads truncated data, because `input.RecursionBudget()` was left decremented and/or `current_limit_` was left constrained from frame 1's failed parse.
4. Compare against a control run using two separate `CodedInputStream` instances (one per frame), which succeeds, to prove that the corruption is caused specifically by state persisting across reuse of one stream object following the missing decrement/pop on the error path.

This proof of concept is described but not run in this session; a background engineering session with build/test tooling would be required to execute and confirm the exact failure mode and byte-for-byte reproduction.

### Citations

**File:** src/google/protobuf/io/coded_stream.h (L393-421)
```text
  // Sets the maximum recursion depth.  The default is 100.
  void SetRecursionLimit(int limit);
  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD int RecursionBudget() {
    return recursion_budget_;
  }

  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD static int GetDefaultRecursionLimit() {
    return default_recursion_limit_;
  }

  // Increments the current recursion depth.  Returns true if the depth is
  // under the limit, false if it has gone over.
  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD bool IncrementRecursionDepth();

  // Decrements the recursion depth if possible.
  void DecrementRecursionDepth();

  // Decrements the recursion depth blindly.  This is faster than
  // DecrementRecursionDepth().  It should be used only if all previous
  // increments to recursion depth were successful.
  void UnsafeDecrementRecursionDepth();

  // Shorthand for make_pair(PushLimit(byte_limit), --recursion_budget_).
  // Using this can reduce code size and complexity in some cases.  The caller
  // is expected to check that the second part of the result is non-negative (to
  // bail out if the depth of recursion is too high) and, if all is well, to
  // later pass the first part of the result to PopLimit() or similar.
  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD std::pair<CodedInputStream::Limit, int>
  IncrementRecursionDepthAndPushLimit(int byte_limit);
```

**File:** src/google/protobuf/io/coded_stream.cc (L146-149)
```text
std::pair<CodedInputStream::Limit, int>
CodedInputStream::IncrementRecursionDepthAndPushLimit(int byte_limit) {
  return std::make_pair(PushLimit(byte_limit), --recursion_budget_);
}
```

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

**File:** src/google/protobuf/wire_format_lite.h (L1190-1214)
```text
template <typename MessageType>
inline bool WireFormatLite::ReadGroup(int field_number,
                                      io::CodedInputStream* input,
                                      MessageType* value) {
  if (!input->IncrementRecursionDepth()) return false;
  if (!value->MergePartialFromCodedStream(input)) return false;
  input->UnsafeDecrementRecursionDepth();
  // Make sure the last thing read was an end tag for this group.
  if (!input->LastTagWas(MakeTag(field_number, WIRETYPE_END_GROUP))) {
    return false;
  }
  return true;
}
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

**File:** src/google/protobuf/parse_context.h (L1431-1443)
```text
template <typename Func>
[[nodiscard]] PROTOBUF_ALWAYS_INLINE const char*
ParseContext::ParseLengthDelimitedInlined(const char* ptr, const Func& func) {
  LimitToken old;
  ptr = ReadSizeAndPushLimitAndDepthInlined(ptr, &old);
  if (ptr == nullptr) return ptr;
  auto old_depth = depth_;
  PROTOBUF_ALWAYS_INLINE_CALL ptr = func(ptr);
  if (ptr != nullptr) ABSL_DCHECK_EQ(old_depth, depth_);
  depth_++;
  if (!PopLimit(std::move(old))) return nullptr;
  return ptr;
}
```
