### Title
Length-Delimited `bytes`/`string` List Fields Validated Against Whole-Buffer Length Instead of Enclosing Message Limit — `ArrayDecoders.decodeBytesList` / `decodeStringList` (File: `java/core/src/main/java/com/google/protobuf/ArrayDecoders.java`)

### Summary
The external report's failed invariant is: a length/size value taken from attacker-controlled input is used to slice out a sub-region of data without first validating it against the *logically correct* boundary (the expected 68-byte `_callData`), only relying on looser checks, which lets malformed/incorrectly-scoped data be processed as if it were well-formed. The closest transferable analog in this Protobuf checkout is in the Java Lite array-based wire parser: `decodeBytesList`, `decodeStringList`, and `decodeStringListRequireUtf8` validate a length-delimited element's length against `data.length` (the whole backing array) rather than against `limit` (the enclosing message/sub-message boundary) that is passed into the very same function as a parameter.

### Finding Description
`decodeLengthPrefixVarint` is the shared length-validation primitive: [1](#0-0) 
It only checks that `length` fits within the caller-supplied `limit` argument.

However, the repeated `bytes`/`string` decoders that are supposed to enforce the *enclosing message's* limit instead pass `data.length` as that `limit` argument, not the real `limit` variable that represents where the current message/field region actually ends: [2](#0-1) [3](#0-2) [4](#0-3) 

The real `limit` parameter (representing the correct sub-message/field boundary) is only used afterward in the `while (position < limit)` loop condition to decide whether to keep scanning for repeated tags — it is never used to bound the length of an individual element's contents. Compare this to `decodeMessageField`-style parsing, which pushes/pops an explicit limit for sub-messages; here, a maliciously large length value on a `bytes`/`string` element can cause `position` to run past the intended `limit` (into bytes belonging to a sibling field, a parent message's trailing fields, or unrelated trailing data), while remaining `<= data.length`, so `decodeLengthPrefixVarint`'s check silently passes.

This mirrors the reported invariant failure exactly: the code has the correct boundary value available (`limit`, analogous to the expected 68-byte `_callData`), but validates the attacker-supplied length against a looser, unrelated bound (`data.length`, analogous to "no length check at all" before consuming the payload).

### Impact Explanation
If exploitable, this could allow one field's declared length to "smuggle" data from adjacent regions of the same backing array into a `bytes`/`string` element, producing a parsed message whose field values do not correspond to how the message was actually encoded — a data-integrity failure in the parsed object graph, not merely a crash. Because IndexOutOfBoundsException handling wraps public entry points (per the class-level comment noting this design), truly out-of-array reads are converted to `InvalidProtocolBufferException`; the residual risk is specifically the case where the read stays inside `data.length` but crosses the *intended* sub-message boundary, corrupting which bytes are attributed to which field. This is a Medium-severity integrity concern, consistent with the source report's classification, since it does not itself provide out-of-bounds memory access or RCE.

### Likelihood Explanation
This code path executes on every repeated `bytes`/`string` field parsed through the Lite array-based decoder for any message with such fields, so the trigger condition (a length prefix that exceeds the enclosing sub-message's real limit but stays within the overall buffer) is easily attacker-constructible in a bounded, valid-looking Protobuf binary payload. However, I was not able to fully confirm within the available context whether an outer caller (e.g., `MessageSchema`) re-validates `position` against the true limit immediately after each `decodeBytesList`/`decodeStringList` call returns, which would neutralize the impact by rejecting the message post-hoc. This uncertainty should be resolved by tracing `MessageSchema.java`'s repeated-field dispatch (the exact call sites) before treating this as a confirmed, exploitable boundary-crossing bug rather than a defense-in-depth gap.

### Recommendation
Pass the actual enclosing `limit` (not `data.length`) into `decodeLengthPrefixVarint` calls inside `decodeBytesList`, `decodeStringList`, and `decodeStringListRequireUtf8`, so that an individual element's length can never cause `position` to exceed the boundary of the message/field region it belongs to — mirroring the fix recommended in the source report (validate the length against the expected/enclosing boundary rather than a looser bound before processing the data).

### Proof of Concept
I was unable to construct and run an actual minimal reproduction within this session (no code-execution tool available here), so this cannot be presented as a verified finding — it is a code-pattern-level analog identified by static inspection of `ArrayDecoders.java`. A concrete PoC would need to: (1) build a nested message where an inner sub-message contains a repeated `bytes` field, (2) encode that repeated `bytes` field's length prefix to a value that exceeds the inner sub-message's remaining bytes but stays within the overall serialized buffer, and (3) parse it via the Lite runtime to check whether the resulting field value contains bytes from beyond the sub-message boundary, and whether the overall parse still reports success. This step is necessary to confirm exploitability and should be performed in a follow-up with actual tooling before treating this as more than a plausible analog.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/ArrayDecoders.java (L113-123)
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
    return position;
  }
```

**File:** java/core/src/main/java/com/google/protobuf/ArrayDecoders.java (L601-632)
```java
  /** Decodes a repeated string field. Returns the position after all read values. */
  @SuppressWarnings("unchecked")
  static int decodeStringList(
      int tag, byte[] data, int position, int limit, ProtobufList<?> list, Registers registers)
      throws InvalidProtocolBufferException {
    final ProtobufList<String> output = (ProtobufList<String>) list;
    position = decodeLengthPrefixVarint(data, position, data.length, registers);
    final int length = registers.int1;
    if (length == 0) {
      output.add("");
    } else {
      String value = new String(data, position, length, StandardCharsets.UTF_8);
      output.add(value);
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
        output.add("");
      } else {
        String value = new String(data, position, nextLength, StandardCharsets.UTF_8);
        output.add(value);
        position += nextLength;
      }
    }
    return position;
  }
```

**File:** java/core/src/main/java/com/google/protobuf/ArrayDecoders.java (L637-673)
```java
  @SuppressWarnings("unchecked")
  static int decodeStringListRequireUtf8(
      int tag, byte[] data, int position, int limit, ProtobufList<?> list, Registers registers)
      throws InvalidProtocolBufferException {
    final ProtobufList<String> output = (ProtobufList<String>) list;
    position = decodeLengthPrefixVarint(data, position, data.length, registers);
    final int length = registers.int1;
    if (length == 0) {
      output.add("");
    } else {
      if (!Utf8.isValidUtf8(data, position, position + length)) {
        throw InvalidProtocolBufferException.invalidUtf8();
      }
      String value = new String(data, position, length, StandardCharsets.UTF_8);
      output.add(value);
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
        output.add("");
      } else {
        if (!Utf8.isValidUtf8(data, position, position + nextLength)) {
          throw InvalidProtocolBufferException.invalidUtf8();
        }
        String value = new String(data, position, nextLength, StandardCharsets.UTF_8);
        output.add(value);
        position += nextLength;
      }
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
