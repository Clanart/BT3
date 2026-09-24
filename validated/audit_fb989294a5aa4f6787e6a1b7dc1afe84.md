Given the nature of this report, it describes a smart-contract accounting bug in Solidity (Union Finance's `UserManager.sol`/`AssetManager.sol`): a partial-withdrawal remainder from a money-market loop is silently dropped, causing the user's staked-balance bookkeeping to diverge from the actual amount transferred. Let me verify there's no meaningful parsing-layer analog in this Protobuf checkout before concluding.### Title
Wrapper-type slow-path parsers in C# (`Int32Value`/`UInt32Value`/`Int64Value`/`UInt64Value`/`FloatValue`/`DoubleValue`) do not verify that the sub-message boundary was consumed exactly, allowing a crafted length prefix to make the parser silently read across the field boundary - (File: `csharp/src/Google.Protobuf/ParsingPrimitivesWrappers.cs`)

### Summary
The reported Union Finance bug is a "declared amount vs. actually-consumed amount" mismatch: the code computes a `remaining` quantity that should reach exactly zero, but proceeds to finalize state as if it always does, without checking. The Protobuf analog is in the C# wrapper-type "slow path" parsers, which compute a local `finalBufferPos` boundary for a length-delimited wrapper submessage (e.g. `google.protobuf.UInt32Value`) and loop until the current position is `>= finalBufferPos`, but never verify that the position lands *exactly* on `finalBufferPos`. Unlike the equivalent "fast path" in the same file, which explicitly checks `state.bufferPos != finalBufferPos` and falls back on mismatch, and unlike the generic `ReadMessage`/`ReadMapEntry` machinery which pushes a real stream limit and calls `SegmentedBufferHelper.IsReachedLimit` afterward, the slow-path wrapper readers never push an actual `CodedInputStream` limit and never check the post-condition.

### Finding Description
`ReadUInt32WrapperSlow`, `ReadUInt64WrapperSlow`, `ReadFloatWrapperSlow`, and `ReadDoubleWrapperSlow` compute: [1](#0-0) 

```
int finalBufferPos = state.totalBytesRetired + state.bufferPos + length;
...
do {
    if (ParsingPrimitives.ParseTag(...) == 8) { result = ParsingPrimitives.ParseRawVarint32(...); }
    else { ParsingPrimitivesMessages.SkipLastField(...); }
} while (state.totalBytesRetired + state.bufferPos < finalBufferPos);
return result;
```

`finalBufferPos` is only a locally-computed target, not an actual limit pushed onto the parser state (`state.currentLimit` is inherited unchanged from the enclosing message). `ParsingPrimitives.ParseTag`, `ParseRawVarint32`, `ParseDouble`, and `ParsingPrimitivesMessages.SkipLastField` (which itself calls `ParseLength`/`SkipRawBytes` for length-delimited unknown fields) all operate against the shared buffer with no awareness of `finalBufferPos`: [2](#0-1) 

If an attacker crafts an unknown field inside the wrapper submessage whose own length-delimited payload (or varint) extends past `finalBufferPos`, `SkipLastField`/`ParseRawVarint32` will happily consume bytes that logically belong to the *next* field of the enclosing message. The `while` loop only tests `<`, so once the position passes (not lands on) `finalBufferPos`, the loop simply exits and the function returns whatever `result` happened to be set to - there is no post-condition check analogous to `remaining == 0`.

By contrast:
- The fast path for the same wrapper types explicitly validates the exact boundary and falls back if it's violated: `if (state.bufferPos != finalBufferPos) { ...; return ReadUInt32WrapperSlow(...); }` [3](#0-2) 
- The generic embedded-message reader (`ReadMessage`) pushes a real limit and explicitly checks it was reached exactly, throwing `TruncatedMessage` otherwise: [4](#0-3) 

So the wrapper "slow path" is the one place where the "declared length vs. actually consumed length" invariant is neither enforced via a pushed limit nor verified after the fact - directly analogous to `AssetManager.withdraw`'s `remaining` not being checked/returned before `UserManager.unstake` finalizes state.

### Impact Explanation
This is a parsing-correctness/integrity issue, not a crash or memory-safety bug (bounded, ordinary-client input via a supported public parse API - `ParseFrom`/wrapper field decoding). A crafted message can cause the parser to attribute bytes belonging to one logical field to a different field boundary within the wrapper submessage handling, potentially corrupting how subsequent sibling fields of the enclosing message are interpreted (silent data-integrity divergence) while the overall parse can still appear "successful" if the attacker balances total byte counts so the enclosing message's own outer limit check (which does validate exactly) is still satisfied. This matches a Medium-severity, non-crash integrity concern: consuming-application logic that trusts the parsed field values could receive semantically wrong data without any exception being raised.

### Likelihood Explanation
Reachable only for consumers of the C# `Google.Protobuf` wrapper types (`google.protobuf.*Value`) parsed via `CodedInputStream`/legacy (non-`ParseContext`) APIs that hit these "slow path" helper methods (large/segmented buffers, or malformed short-circuit conditions that force the fast path to bail out). It requires the attacker to control the raw bytes of a wrapper-typed field precisely enough to embed an over-length unknown sub-field - feasible for any client sending arbitrary bounded binary protobuf to a public parse API using a schema containing wrapper-typed fields.

### Recommendation
In `ReadUInt32WrapperSlow`, `ReadUInt64WrapperSlow`, `ReadFloatWrapperSlow`, and `ReadDoubleWrapperSlow`, either:
1. Push an actual limit via `SegmentedBufferHelper.PushLimit`/`PopLimit` for `length` bytes (as `ReadMessage` does) so that `ParseTag`/`SkipLastField`/varint reads are bounded and any overrun throws `TruncatedMessage`, or
2. After the loop, explicitly assert `state.totalBytesRetired + state.bufferPos == finalBufferPos` and throw `InvalidProtocolBufferException` otherwise - mirroring the equality check already present in the fast-path implementations.

### Proof of Concept
Conceptual encoding for a `UInt32Value`-typed field (field tag omitted, only the wrapper submessage payload shown), designed to reach `ReadUInt32WrapperSlow` (e.g., via a segmented/multi-buffer `CodedInputStream` so the fast path's `state.bufferPos + 12 <= state.bufferSize` guard fails):

```
length = N            // declared wrapper submessage length
  tag = (field 2, wiretype LEN)  // unexpected/unknown field, NOT tag 8
  sub_length = M       // M crafted so that current_pos + M > finalBufferPos
  <M bytes of payload> // overruns into what should be the next field of the parent message
```//
`SkipLastField` -> `ParseLength`/`SkipRawBytes(length=M)` will advance `state.bufferPos` past `finalBufferPos` with no bounds check tied to `finalBufferPos`, and the `do...while` loop exits on the `<` test without verifying `== finalBufferPos`. I was not able to execute this scenario in a live test harness (no execution environment available in this session); the analysis above is based on static code review of the cited files, and confirming exploitability end-to-end (i.e., that the outer message's final `IsReachedLimit` check can still be satisfied despite the internal boundary corruption) would require building and running a minimal C# repro with a custom multi-segment `CodedInputStream`/`ReadOnlySequence<byte>` input, which should be done to fully validate before treating this as a confirmed, actionable finding rather than a plausible analog.

### Citations

**File:** csharp/src/Google.Protobuf/ParsingPrimitivesWrappers.cs (L151-164)
```csharp
                int finalBufferPos = state.bufferPos + length;
                if (buffer[state.bufferPos++] != expectedTag)
                {
                    state.bufferPos = pos0;
                    return ReadUInt32WrapperSlow(ref buffer, ref state);
                }
                var result = ParsingPrimitives.ParseRawVarint32(ref buffer, ref state);
                // Verify this message only contained a single field.
                if (state.bufferPos != finalBufferPos)
                {
                    state.bufferPos = pos0;
                    return ReadUInt32WrapperSlow(ref buffer, ref state);
                }
                return result;
```

**File:** csharp/src/Google.Protobuf/ParsingPrimitivesWrappers.cs (L172-195)
```csharp
        internal static uint? ReadUInt32WrapperSlow(ref ReadOnlySpan<byte> buffer, ref ParserInternalState state)
        {
            int length = ParsingPrimitives.ParseLength(ref buffer, ref state);
            if (length == 0)
            {
                return 0;
            }
            int finalBufferPos = state.totalBytesRetired + state.bufferPos + length;
            uint result = 0;
            do
            {
                // field=1, type=varint = tag of 8
                if (ParsingPrimitives.ParseTag(ref buffer, ref state) == 8)
                {
                    result = ParsingPrimitives.ParseRawVarint32(ref buffer, ref state);
                }
                else
                {
                    ParsingPrimitivesMessages.SkipLastField(ref buffer, ref state);
                }
            }
            while (state.totalBytesRetired + state.bufferPos < finalBufferPos);
            return result;
        }
```

**File:** csharp/src/Google.Protobuf/ParsingPrimitivesMessages.cs (L26-54)
```csharp
        public static void SkipLastField(ref ReadOnlySpan<byte> buffer, ref ParserInternalState state)
        {
            if (state.lastTag == 0)
            {
                throw new InvalidOperationException("SkipLastField cannot be called at the end of a stream");
            }
            switch (WireFormat.GetTagWireType(state.lastTag))
            {
                case WireFormat.WireType.StartGroup:
                    SkipGroup(ref buffer, ref state, state.lastTag);
                    break;
                case WireFormat.WireType.EndGroup:
                    throw new InvalidProtocolBufferException(
                        "SkipLastField called on an end-group tag, indicating that the corresponding start-group was missing");
                case WireFormat.WireType.Fixed32:
                    ParsingPrimitives.ParseRawLittleEndian32(ref buffer, ref state);
                    break;
                case WireFormat.WireType.Fixed64:
                    ParsingPrimitives.ParseRawLittleEndian64(ref buffer, ref state);
                    break;
                case WireFormat.WireType.LengthDelimited:
                    var length = ParsingPrimitives.ParseLength(ref buffer, ref state);
                    ParsingPrimitives.SkipRawBytes(ref buffer, ref state, length);
                    break;
                case WireFormat.WireType.Varint:
                    ParsingPrimitives.ParseRawVarint32(ref buffer, ref state);
                    break;
            }
        }
```

**File:** csharp/src/Google.Protobuf/ParsingPrimitivesMessages.cs (L94-114)
```csharp
        public static void ReadMessage(ref ParseContext ctx, IMessage message)
        {
            int length = ParsingPrimitives.ParseLength(ref ctx.buffer, ref ctx.state);
            if (ctx.state.recursionDepth >= ctx.state.recursionLimit)
            {
                throw InvalidProtocolBufferException.RecursionLimitExceeded();
            }
            int oldLimit = SegmentedBufferHelper.PushLimit(ref ctx.state, length);
            ++ctx.state.recursionDepth;

            ReadRawMessage(ref ctx, message);

            CheckReadEndOfStreamTag(ref ctx.state);
            // Check that we've read exactly as much data as expected.
            if (!SegmentedBufferHelper.IsReachedLimit(ref ctx.state))
            {
                throw InvalidProtocolBufferException.TruncatedMessage();
            }
            --ctx.state.recursionDepth;
            SegmentedBufferHelper.PopLimit(ref ctx.state, oldLimit);
        }
```
