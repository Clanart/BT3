### Title
Unbalanced recursion-depth counter and byte-limit stack on exception in `GPBCodedInputStream` message/group parsing - (File: `objectivec/GPBCodedInputStream.m`)

### Summary
The Sherlock report flags that `UXDToken.mint`/`burn` mutate the `localMintAmount` accounting state *before* calling a function (`_mint`/`_burn`) that can revert, so the counter update is not exception-safe with respect to the operation it accounts for. The transferable invariant is: **a stateful counter/limit that tracks an in-progress operation must be restored if the operation it guards fails**, otherwise the counter drifts and corrupts later accounting/security decisions. In `GPBCodedInputStream.m`, `readMessage:`, `readGroup:`, and `readMapEntry:` increment `state_.recursionDepth` (and, for the length-delimited variants, push a byte limit) *before* invoking `mergeFromCodedInputStream:`, which can raise an `NSException` on malformed input, but the corresponding decrement/pop is not protected by `@try/@finally`, so a thrown exception leaves the stream's recursion-depth counter and limit stack permanently unbalanced.

### Finding Description
`readGroup:message:extensionRegistry:` and `readMessage:extensionRegistry:` follow this pattern: [1](#0-0) 

Both increment `state_.recursionDepth` and (for `readMessage:`) push a new `currentLimit` via `GPBCodedInputStreamPushLimit`, then call `mergeFromCodedInputStream:extensionRegistry:endingTag:`, and only afterward decrement/pop. Parsing errors in protobuf's Objective-C runtime are surfaced as `NSException`s raised via `GPBRaiseStreamError` (e.g. on an unexpected tag): [2](#0-1) 

If `mergeFromCodedInputStream:` throws while decoding the nested message (malformed tag, invalid varint, oversized field, etc.), the `--state_.recursionDepth;` and `GPBCodedInputStreamPopLimit(&state_, oldLimit);` statements are skipped because there is no exception-safety wrapper — unlike the equivalent correct pattern used elsewhere in the same codebase, where `initWithData:parentRecursionDepth:` explicitly wraps the recursion check in `@try/@catch` specifically to avoid leaking state on failure: [3](#0-2) 

and where the C#/C++ implementations deliberately restore recursion depth in a `finally`/unconditional-after-call fashion so state stays correct even on parse failure: [4](#0-3) [5](#0-4) [6](#0-5) 

`readMapEntry:extensionRegistry:field:parentMessage:` has the identical unprotected pattern: [7](#0-6) 

`GPBCodedInputStream` is a public, directly-usable class (`+streamWithData:`) intended to support reading multiple length-prefixed messages sequentially from the same stream instance. If application code (a trusted consumer, per the exposure assumption) wraps a per-item `mergeFromCodedInputStream:` call in `@try/@catch` to skip a malformed item and continue reading subsequent messages from the same stream — a natural usage of the public API — the stream's `recursionDepth` and limit stack are left corrupted for all following reads on that instance, exactly analogous to `localMintAmount` drifting because the accounting update wasn't rolled back when the guarded operation failed.

### Impact Explanation
A corrupted `recursionDepth` counter can either (a) permanently and erroneously reduce the available recursion budget on the stream, causing legitimate subsequent deeply-nested (but valid) messages parsed from the same stream to be spuriously rejected (denial of correct parsing), or (b) in the `readMessage:`/`readMapEntry:` cases, leave `currentLimit_` pinned to the truncated boundary of the failed submessage, corrupting the byte-accounting state used to bound subsequent reads on the same stream (`ConsumedEntireMessage`/`ExpectAtEnd` results become unreliable). This is a data-integrity/availability issue in the parser's internal bookkeping rather than memory corruption, so it is scoped as Medium, matching the severity class of the original report (accounting-state drift due to non-exception-safe ordering of state mutation vs. a call that can fail).

### Likelihood Explanation
Requires (1) an attacker-controlled malformed nested message/group/map-entry that triggers an `NSException` mid-parse, and (2) the consuming application catching that exception to continue reading further messages from the same `GPBCodedInputStream` instance rather than discarding it — a pattern explicitly supported by the class's design (streaming multiple delimited messages). Given typical single-shot `parseFromData:`/`mergeFromData:` usage, the corrupted stream is usually discarded immediately after the exception, limiting real-world exposure; but for any code that reuses the stream across a loop of `mergeFrom` calls (a documented, sanctioned usage pattern for `CodedInputStream`), the bug is directly reachable via bounded, attacker-supplied binary protobuf input.

### Recommendation
Wrap the recursion-depth increment/decrement and limit push/pop pairs in `readMessage:extensionRegistry:`, `readGroup:message:extensionRegistry:`, and `readMapEntry:extensionRegistry:field:parentMessage:` in `@try/@finally` blocks (mirroring the exception-safety already used in `initWithData:parentRecursionDepth:`), so that `state_.recursionDepth` is decremented and any pushed limit is popped regardless of whether `mergeFromCodedInputStream:`/`GPBDictionaryReadEntry` throws.

### Proof of Concept
Not independently executed (no test harness run in this analysis); the finding is based on direct code inspection of `objectivec/GPBCodedInputStream.m:529-567` showing the increment/push occurs unconditionally before the potentially-throwing `mergeFromCodedInputStream:`/`GPBDictionaryReadEntry` call, with the matching decrement/pop reachable only on the non-throwing path — contrasted with the exception-safe pattern demonstrably used at `objectivec/GPBCodedInputStream.m:392-413` and in the C#/C++ implementations cited above. A concrete repro would construct a `GPBCodedInputStream` over bytes for two concatenated length-delimited messages where the first contains a malformed inner tag causing `mergeFromCodedInputStream:` to raise `NSException`, catch that exception in a loop, and then observe `state_.recursionDepth`/`currentLimit` are not reset before attempting to read the second (well-formed) message from the same stream instance.

### Citations

**File:** objectivec/GPBCodedInputStream.m (L366-370)
```text
void GPBCodedInputStreamCheckLastTagWas(GPBCodedInputStreamState *state, int32_t value) {
  if (state->lastTag != value) {
    GPBRaiseStreamError(GPBCodedInputStreamErrorInvalidTag, @"Unexpected tag read");
  }
}
```

**File:** objectivec/GPBCodedInputStream.m (L392-413)
```text
- (instancetype)initWithData:(NSData *)data parentRecursionDepth:(NSUInteger)parentDepth {
  if ((self = [self initWithData:data])) {
    // The parent stream had already entered `parentDepth` nested parses; we
    // are about to begin one more level in this child stream, so seed the
    // depth accordingly and verify the limit before parsing starts. This
    // matches the convention used by the C++ ParseContext spawn helper,
    // which increments and checks the depth before recursing into a payload
    // that has been read into a fresh buffer.
    state_.recursionDepth = parentDepth + 1;
    @try {
      CheckRecursionLimit(&state_);
    } @catch (NSException *exception) {
      // If CheckRecursionLimit raises an exception (when recursion depth exceeds
      // kDefaultRecursionLimit), `self` will not be returned to the caller.
      // Explicitly release `self` here to avoid a memory leak before re-throwing.
      [self release];
      self = nil;
      @throw;
    }
  }
  return self;
}
```

**File:** objectivec/GPBCodedInputStream.m (L529-551)
```text
- (void)readGroup:(int32_t)fieldNumber
              message:(GPBMessage *)message
    extensionRegistry:(id<GPBExtensionRegistry>)extensionRegistry {
  CheckRecursionLimit(&state_);
  ++state_.recursionDepth;
  [message mergeFromCodedInputStream:self
                   extensionRegistry:extensionRegistry
                           endingTag:GPBWireFormatMakeTag(fieldNumber, GPBWireFormatEndGroup)];
  --state_.recursionDepth;
}

- (void)readMessage:(GPBMessage *)message
    extensionRegistry:(id<GPBExtensionRegistry>)extensionRegistry {
  CheckRecursionLimit(&state_);
  uint64_t length = GPBCodedInputStreamReadUInt64(&state_);
  CheckFieldSize(length);
  size_t length2 = (size_t)length;  // Cast safe on 32bit because of CheckFieldSize() above.
  size_t oldLimit = GPBCodedInputStreamPushLimit(&state_, length2);
  ++state_.recursionDepth;
  [message mergeFromCodedInputStream:self extensionRegistry:extensionRegistry endingTag:0];
  --state_.recursionDepth;
  GPBCodedInputStreamPopLimit(&state_, oldLimit);
}
```

**File:** objectivec/GPBCodedInputStream.m (L553-567)
```text
- (void)readMapEntry:(id)mapDictionary
    extensionRegistry:(id<GPBExtensionRegistry>)extensionRegistry
                field:(GPBFieldDescriptor *)field
        parentMessage:(GPBMessage *)parentMessage {
  CheckRecursionLimit(&state_);
  uint64_t length = GPBCodedInputStreamReadUInt64(&state_);
  CheckFieldSize(length);
  size_t length2 = (size_t)length;  // Cast safe on 32bit because of CheckFieldSize() above.
  size_t oldLimit = GPBCodedInputStreamPushLimit(&state_, length2);
  ++state_.recursionDepth;
  GPBDictionaryReadEntry(mapDictionary, self, extensionRegistry, field, parentMessage);
  GPBCodedInputStreamCheckLastTagWas(&state_, 0);
  --state_.recursionDepth;
  GPBCodedInputStreamPopLimit(&state_, oldLimit);
}
```

**File:** csharp/src/Google.Protobuf/JsonParser.cs (L138-144)
```csharp
            tokenizer.RecursionDepth++;

            // try/finally used in order to decrement the recursion depth regardless of outcome.
            // If an exception is thrown, the recursion depth is irrelevant anyway - but as the method
            // has multiple return statements, this is the simplest way of ensuring the recursion depth
            // is always decremented. An alternative would be to use a local function.
            try
```

**File:** csharp/src/Google.Protobuf/JsonParser.cs (L206-209)
```csharp
            finally
            {
                tokenizer.RecursionDepth--;
            }
```

**File:** src/google/protobuf/parse_context.cc (L473-483)
```text
const char* ParseContext::ParseMessage(MessageLite* msg, const char* ptr) {
  LimitToken old;
  ptr = ReadSizeAndPushLimitAndDepth(ptr, &old);
  if (ptr == nullptr) return ptr;
  auto old_depth = depth_;
  ptr = msg->_InternalParse(ptr, this);
  if (ptr != nullptr) ABSL_DCHECK_EQ(old_depth, depth_);
  depth_++;
  if (!PopLimit(std::move(old))) return nullptr;
  return ptr;
}
```
