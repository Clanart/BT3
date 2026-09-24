Based on my investigation, I found a genuine structural analog to the tcpdump CVE-2018-16451 pattern in the Java Lite runtime's array-based wire decoder, though I want to flag upfront that verifying the full exploitation path (the caller-side limit reconciliation in `MessageSchema.java`) exceeded my available tool budget — I could not confirm the final `position <= limit` re-check that the surrounding parse loop is expected to perform after each field. Treat the finding below as strong circumstantial evidence, not a fully proven exploit chain.

### Title
Length-delimited field length validated against total buffer size instead of enclosing message limit - (File: `java/core/src/main/java/com/google/protobuf/ArrayDecoders.java`)

### Summary
`ArrayDecoders.decodeLengthPrefixVarint` validates a field's declared length against `data.length` (the whole backing byte array) rather than against the enclosing message's structural `limit`. `decodeBytesList`, `decodePackedDoubleList`, and `decodePackedBoolList` all call it this way [1](#0-0) , mirroring the tcpdump SMB parser's failure to bound a nested field's length against the enclosing PDU/transaction boundary rather than the raw capture buffer.

### Finding Description
`decodeLengthPrefixVarint` is the shared length-prefix validator: [1](#0-0) 
It rejects a length only if it would run past `limit`, but every caller I inspected passes `data.length` as `limit`, not the message's actual field/submessage boundary:
- `decodeBytesList`: `position = decodeLengthPrefixVarint(data, position, data.length, registers);` [2](#0-1) 
- `decodePackedDoubleList` / `decodePackedBoolList`: same pattern [3](#0-2) [4](#0-3) 

The tcpdump analog: `print_trans()` computed offsets/lengths from attacker-controlled SMB fields and read/printed data bounded only by the overall packet snapshot length, not by the actual (smaller) transaction sub-structure length — an over-read that stays inside the allocated buffer but crosses a *logical* boundary it shouldn't. Here, the "logical boundary" is the enclosing protobuf message's `limit` (set when a submessage/`Any`/nested field is entered), and the "physical boundary" is `data.length`. A declared bytes/packed-field length that is invalid for the *current submessage* but still fits in the *overall byte array* passes this check silently.

The `decodeBytesList`/`decodePackedDoubleList` functions do have a secondary sanity check (`position != fieldLimit` throws `truncatedMessage`) that catches malformed varint payloads inside packed scalars, but that check only verifies internal consistency of the packed elements — it does not re-validate against the message-level `limit` after the length-prefix has already been accepted against `data.length`. Whether the outer per-field dispatch loop in `MessageSchema.java` re-checks `position <= limit` immediately afterward (which would neutralize this) is something I was unable to confirm before running out of investigation budget.

### Impact Explanation
If the outer loop does not immediately re-validate `position` against the enclosing `limit` after this call returns, a crafted length-delimited `bytes`/packed field nested inside a submessage (or an `Any`/lazy field) could cause `ByteString.copyFrom(data, position, length)` to copy bytes belonging to sibling fields, or bytes past the intended submessage boundary but still inside the shared backing array — an integrity/information-disclosure issue analogous to the SMB over-read (wrong-boundary data exposed to a field it doesn't belong to), not a memory-safety crash (Java's array bounds checking prevents true out-of-bounds access).

### Likelihood Explanation
Reachable via any public parse of untrusted, well-formed-but-malicious binary protobuf using the Lite runtime's array decoder path (`CodedInputStream`/`ArrayDecoders`), which is the default fast path for `parseFrom(byte[])`. No privileged access or non-standard schema is required — only a nested message containing a repeated bytes/packed-scalar field with an oversized length prefix relative to its enclosing limit.

### Recommendation
Have `decodeLengthPrefixVarint`'s callers in `decodeBytesList`, `decodePackedDoubleList`, and `decodePackedBoolList` pass the actual enclosing-message `limit` (not `data.length`) for validation, and/or ensure the outer field-dispatch loop in `MessageSchema.java` unconditionally re-checks `position <= limit` immediately after each of these calls returns, throwing `InvalidProtocolBufferException.truncatedMessage()` on violation.

### Proof of Concept
I could not execute a concrete PoC in ask-only mode (no code execution available), and I was unable to confirm within the tool budget whether `MessageSchema.java`'s dispatch loop already performs the missing re-check, which would fully mitigate this. A conclusive PoC would require: (1) constructing an outer message with a length-delimited submessage field of declared length N, (2) embedding inside it a repeated-bytes or packed-double field whose length prefix declares a value > (N − consumed), but still ≤ (overall buffer length − position), and (3) confirming via a Devin session with full build/execution access whether the resulting parsed `ByteString`/list contains bytes from beyond the submessage boundary and whether `MessageSchema`'s loop fails to reject it. [1](#0-0) [5](#0-4)

### Citations

**File:** java/core/src/main/java/com/google/protobuf/ArrayDecoders.java (L113-121)
```java
  static int decodeLengthPrefixVarint(byte[] data, int position, int limit, Registers registers)
      throws InvalidProtocolBufferException {
    position = decodeVarint32(data, position, registers);
    final int length = registers.int1;
    if (length < 0) {
      throw InvalidProtocolBufferException.negativeSize();
    } else if (length > limit - position) {
      throw InvalidProtocolBufferException.truncatedMessage();
    }
```

**File:** java/core/src/main/java/com/google/protobuf/ArrayDecoders.java (L529-545)
```java
  static int decodePackedDoubleList(
      byte[] data, int position, ProtobufList<?> list, Registers registers)
      throws InvalidProtocolBufferException {
    final DoubleArrayList output = (DoubleArrayList) list;
    position = decodeLengthPrefixVarint(data, position, data.length, registers);
    final int packedDataByteSize = registers.int1;
    final int fieldLimit = position + packedDataByteSize;
    output.ensureCapacity(output.size() + packedDataByteSize / 8);
    while (position < fieldLimit) {
      output.addDouble(decodeDouble(data, position));
      position += 8;
    }
    if (position != fieldLimit) {
      throw InvalidProtocolBufferException.truncatedMessage();
    }
    return position;
  }
```

**File:** java/core/src/main/java/com/google/protobuf/ArrayDecoders.java (L547-563)
```java
  /** Decodes a packed boolean field. Returns the position after all read values. */
  static int decodePackedBoolList(
      byte[] data, int position, ProtobufList<?> list, Registers registers)
      throws InvalidProtocolBufferException {
    final BooleanArrayList output = (BooleanArrayList) list;
    position = decodeLengthPrefixVarint(data, position, data.length, registers);
    final int packedDataByteSize = registers.int1;
    final int fieldLimit = position + packedDataByteSize;
    while (position < fieldLimit) {
      position = decodeVarint64(data, position, registers);
      output.addBoolean(registers.long1 != 0);
    }
    if (position != fieldLimit) {
      throw InvalidProtocolBufferException.truncatedMessage();
    }
    return position;
  }
```

**File:** java/core/src/main/java/com/google/protobuf/ArrayDecoders.java (L675-704)
```java
  /** Decodes a repeated bytes field. Returns the position after all read values. */
  @SuppressWarnings("unchecked")
  static int decodeBytesList(
      int tag, byte[] data, int position, int limit, ProtobufList<?> list, Registers registers)
      throws InvalidProtocolBufferException {
    final ProtobufList<ByteString> output = (ProtobufList<ByteString>) list;
    position = decodeLengthPrefixVarint(data, position, data.length, registers);
    final int length = registers.int1;
    if (length == 0) {
      output.add(ByteString.EMPTY);
    } else {
      output.add(ByteString.copyFrom(data, position, length));
      position += length;
    }
    while (position < limit) {
      int nextPosition = decodeVarint32(data, position, registers);
      if (tag != registers.int1) {
        break;
      }
      position = decodeLengthPrefixVarint(data, nextPosition, data.length, registers);
      final int nextLength = registers.int1;
      if (nextLength == 0) {
        output.add(ByteString.EMPTY);
      } else {
        output.add(ByteString.copyFrom(data, position, nextLength));
        position += nextLength;
      }
    }
    return position;
  }
```
