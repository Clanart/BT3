## Title
Silent 32-bit truncation of packed-field length in `WireFormat::ParseAndMergeField` causes wire-format misparsing - (File: `src/google/protobuf/wire_format.cc`)

## Summary
`WireFormat::ParseAndMergeField()`, the reflection-based (`Message::MergeFromCodedStream`) binary parser used for dynamic/reflection messages, reads the length prefix of a packed repeated field with `CodedInputStream::ReadVarint32()`, a call that is explicitly documented to *silently truncate* any value that doesn't fit in 32 bits, instead of the size-safe `ReadVarintSizeAsInt()` that the same header tells callers to use "when we are parsing a size from the wire." [1](#0-0) [2](#0-1) 

## Finding Description
The attacker-controlled value here is the varint length prefix that precedes a packed repeated field's payload on the wire. `ParseAndMergeField` reads it like this:

```cpp
} else if (value_format == PACKED_FORMAT) {
    uint32_t length;
    if (!input->ReadVarint32(&length)) return false;
    io::CodedInputStream::Limit limit = input->PushLimit(length);
``` [1](#0-0) 

`CodedInputStream::ReadVarint32()` is documented as: "Read an unsigned integer with Varint encoding, truncating to 32 bits. Reading a 32-bit value is equivalent to reading a 64-bit one and casting it to uint32_t, but may be more efficient." [3](#0-2) 

Immediately following it in the same header is `ReadVarintSizeAsInt()`, with an explicit warning that distinguishes this exact case:
"Reads a varint off the wire into an "int". This should be used for reading sizes off the wire (sizes of strings, submessages, bytes fields, etc)... If its value exceeds the representable value of an integer on this platform, instead of truncating we return false. Truncating (as performed by ReadVarint32() above) is an acceptable approach for fields representing an integer, but when we are parsing a size from the wire, truncating the value would result in us misparsing the payload." [4](#0-3) 

`ParseAndMergeField`'s PACKED_FORMAT branch is exactly the "parsing a size from the wire" case the documentation warns about, yet it uses the unsafe `ReadVarint32` rather than `ReadVarintSizeAsInt`. A varint that encodes a value whose low 32 bits are small (e.g., `0x1_00000005`) is truncated to `5` with no error returned, and `PushLimit(5)` establishes a limit far smaller than what the sender intended, or that does not correspond to any length the sender could have validly meant. Subsequent bytes that were meant to belong to the packed array are then parsed as unrelated top-level tag/value pairs of the containing message, silently changing the field values and message content produced by the parser without an integrity check ever firing.

This is a genuine analog to the PoolTogether `Vault::_transfer` bug: an attacker-controlled numeric value (transfer shares / packed length) is downcast/truncated without a `SafeCast`-equivalent bounds check (`ReadVarintSizeAsInt`/`OpenZeppelin SafeCast`), causing state (token balances / message field contents) to silently diverge from what the wire data logically encoded, rather than the parser rejecting the malformed/oversized input.

Note: the fast-path template parser (`TcParser`/`ParseContext::ReadSize`) used for generated messages already validates the size against `INT_MAX - kSlopBytes` and rejects oversized values. [5](#0-4)  This bug is confined to the legacy reflection-based `WireFormat::ParseAndMergeField` path (used by `Message::MergeFromCodedStream`/`MergePartialFromCodedStream` for dynamic messages, `DynamicMessage`, and any code still going through `Reflection`), which does not share that hardening.

## Impact Explanation
Impact is limited to parsing-integrity/accounting-style corruption of the resulting in-memory message, analogous to the PoolTogether report's "accounting errors": bytes that should have belonged to a packed repeated field are silently reinterpreted as separate top-level fields of the enclosing message (or vice versa, if the truncated value is larger than intended, fewer subsequent bytes are consumed than the sender specified). This can desynchronize the consuming application's view of the message from what was actually sent — e.g., truncated/extra repeated values, or unknown-field misclassification — without any parse error being surfaced. This does not, by itself, corrupt memory or crash the process (`PushLimit`/`BytesUntilLimit` still bound reads to the actual buffer), so it is a data-integrity/parsing-differential issue rather than memory safety.

## Likelihood Explanation
Reachable directly by any ordinary client via `Message::ParseFromString`/`MergeFromCodedStream` on a message with a packable repeated field, whenever the reflection-based parser is exercised (dynamic messages, proto reflection, some `MergeFrom` code paths) rather than the compiled fast-table parser. No privileged access or huge/unbounded payload is required — the crafted varint length prefix is only 5 bytes.

## Recommendation
Replace `input->ReadVarint32(&length)` in the PACKED_FORMAT branch of `WireFormat::ParseAndMergeField` (and the analogous call in `WireFormat::SkipMessageSetField`) with `input->ReadVarintSizeAsInt(&length)`, returning `false` on failure exactly as the size-safe API is designed to do, matching the safety property already enforced by the `TcParser`/`ParseContext::ReadSize` fast path.

## Proof of Concept
Not independently executed in this environment (no filesystem/terminal access from Ask mode), but the mechanism is directly evidenced by the source:
1. Construct a packed field with wire type `WIRETYPE_LENGTH_DELIMITED` whose length prefix is encoded as the 5-byte varint for `0x100000005` (i.e., bytes `85 80 80 80 10`), followed by 5 bytes of packed element data and then additional bytes that encode further top-level fields of the same message.
2. Parse this message through the reflection path (e.g., `DynamicMessage::ParseFromString` or any `Message::MergeFromCodedStream` call that resolves to `WireFormat::ParseAndMergeField`).
3. `ReadVarint32(&length)` truncates `0x100000005` to `5`, so `PushLimit(5)` only covers the first 5 bytes as packed data; the following bytes — meant by the sender to be part of the packed field's payload — are consumed as new top-level tags of the enclosing message, changing the resulting message contents from what the byte stream logically encodes, without any parse failure.

**Caveat:** I did not build/run this against the actual checkout to confirm end-to-end behavior (e.g., exact interaction with `CodedInputStream`'s `total_bytes_limit_`/`current_limit_` bookkeeping), so the PoC should be validated with a live Devin session/test harness before relying on it as a confirmed exploit.

### Citations

**File:** src/google/protobuf/wire_format.cc (L398-401)
```text
  } else if (value_format == PACKED_FORMAT) {
    uint32_t length;
    if (!input->ReadVarint32(&length)) return false;
    io::CodedInputStream::Limit limit = input->PushLimit(length);
```

**File:** src/google/protobuf/io/coded_stream.h (L224-240)
```text
  // Read an unsigned integer with Varint encoding, truncating to 32 bits.
  // Reading a 32-bit value is equivalent to reading a 64-bit one and casting
  // it to uint32_t, but may be more efficient.
  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD bool ReadVarint32(uint32_t* value);
  // Read an unsigned integer with Varint encoding.
  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD bool ReadVarint64(uint64_t* value);

  // Reads a varint off the wire into an "int". This should be used for reading
  // sizes off the wire (sizes of strings, submessages, bytes fields, etc).
  //
  // The value from the wire is interpreted as unsigned.  If its value exceeds
  // the representable value of an integer on this platform, instead of
  // truncating we return false. Truncating (as performed by ReadVarint32()
  // above) is an acceptable approach for fields representing an integer, but
  // when we are parsing a size from the wire, truncating the value would result
  // in us misparsing the payload.
  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD bool ReadVarintSizeAsInt(int* value);
```

**File:** src/google/protobuf/parse_context.cc (L548-566)
```text
std::pair<const char*, int32_t> ReadSizeFallback(const char* p, uint32_t res) {
  for (std::uint32_t i = 1; i < 4; i++) {
    uint32_t byte = static_cast<uint8_t>(p[i]);
    res += (byte - 1) << (7 * i);
    if (ABSL_PREDICT_TRUE(byte < 128)) {
      return {p + i + 1, res};
    }
  }
  std::uint32_t byte = static_cast<uint8_t>(p[4]);
  if (ABSL_PREDICT_FALSE(byte >= 8)) return {nullptr, 0};  // size >= 2gb
  res += (byte - 1) << 28;
  // Protect against sign integer overflow in PushLimit. Limits are relative
  // to buffer ends and ptr could potential be kSlopBytes beyond a buffer end.
  // To protect against overflow we reject limits absurdly close to INT_MAX.
  if (ABSL_PREDICT_FALSE(res > INT_MAX - ParseContext::kSlopBytes)) {
    return {nullptr, 0};
  }
  return {p + 5, res};
}
```
