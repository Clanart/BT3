### Title
`ArrayDecoders.skipField` unbounded-length group skip could hang the Java parser on crafted negative-length input - (File: `java/core/src/main/java/com/google/protobuf/ArrayDecoders.java`)

### Summary
The Shardeum report describes an attacker-controlled numeric value (`toBlock`) that is `parseInt()`'d without range validation and then used to drive a `for` loop increment; once the parsed value falls outside the safe integer range, the increment step becomes a no-op and the loop never terminates, hanging the node. The transferable Protobuf analog is a length/size value read from the wire (a varint that can be crafted to decode to a negative `int`) that is fed into a loop-controlling routine without being validated as non-negative before being used to bound how much of the buffer is consumed, which historically caused `ArrayDecoders.skipField` to loop forever when skipping an unknown/START_GROUP field with a corrupted length-delimited sub-field.

### Finding Description
`ArrayDecoders` is the array-based fast-path binary parser used by `MessageSchema`/`Java Lite` for reading unknown/unrecognized fields off the wire (`WIRETYPE_START_GROUP`, nested `WIRETYPE_LENGTH_DELIMITED`). The repository's own regression test documents the exact failure mode: [1](#0-0) 

This test constructs a payload where a `START_GROUP` tag is followed by a nested `LENGTH_DELIMITED` field whose length is encoded as `-6` via a 5-byte varint (`0xFA 0xFF 0xFF 0xFF 0x0F`, i.e. `parseInt`-equivalent decoding of the varint yields a negative Java `int`). The comment explicitly states this "triggers the infinite loop": the length is attacker-controlled, is not checked for being non-negative before it is used to advance the read cursor, and previously this caused `skipField`'s internal loop (which walks nested fields until it consumes the declared length) to never make forward progress or never satisfy its termination condition — the direct structural analog of the Shardeum `for (i = fromBlock; i <= toBlock; i++)` loop whose increment never satisfies the exit condition once `i` overflows past the safe range.

This mirrors the missing-invariant pattern in the report precisely:
- **Attacker-controlled value:** a wire-format varint interpreted as a signed `int` length, fully controlled by a network peer sending arbitrary bytes into a public parse entrypoint (`Message.parseFrom`/`mergeFrom`).
- **Missing check:** the raw parsed length is not validated to be within a safe/non-negative range before being used to control loop/skip bounds.
- **Failed invariant:** the loop that skips or accounts for the sub-field's bytes assumes monotonic progress toward a limit, but a negative/overflowed length value breaks that assumption, mirroring the JS `parseInt` case where `i++` never converges to `toBlock`.

The fix for this specific ArrayDecoders case (present in this checkout, given the test now asserts `InvalidProtocolBufferException` is thrown rather than hanging) is that negative sizes are now explicitly rejected, as also evidenced by the sibling tests `testException_skipField_negativeSize_standard` and `testException_skipField_negativeSize_crafted`: [2](#0-1) 

I was not able to fully retrieve the current body of `ArrayDecoders.skipField` in this pass (only the surrounding grep matches for `static int skipField` and `WIRETYPE_START_GROUP` were located, not the full implementation), so I cannot show the exact current bounds-check line numbers. This is a known indexing limitation for this file in this session — a full read of `java/core/src/main/java/com/google/protobuf/ArrayDecoders.java` would be needed to confirm the exact guard and line numbers of the fix.

### Impact Explanation
If reachable without the guard, an attacker sending a single crafted message containing a `START_GROUP` field with a nested length-delimited entry with a negative encoded length could hang the parsing thread indefinitely on `Message.parseFrom`/`mergeFrom`, a public, unauthenticated parse API. In server processes that parse untrusted Protobuf messages per-request (e.g., RPC frameworks), this is a single-message denial-of-service against the parsing thread/worker, analogous in class (though not in root cause) to the Shardeum node hang — an attacker-supplied numeric value defeats a loop's termination condition. Given the test file's `@Test(timeout = 1000)` annotation and the explicit "infinite loop" comment, this was treated as a real hang, not just a slow path.

### Likelihood Explanation
This is a Medium/historical-fix scenario in the current codebase: the presence of dedicated regression tests (`testException_skipField_infiniteLoop`, `testException_skipField_negativeSize_standard`, `testException_skipField_negativeSize_crafted`) strongly suggests the vulnerability existed and has since been patched by rejecting negative sizes before they can be used as loop-control values, converting the hang into a thrown `InvalidProtocolBufferException`. Likelihood of this being currently exploitable in this checkout is Low, but the pattern is worth flagging: any future addition or refactor of length-driven skip/read loops (in `ArrayDecoders`, `CodedInputStream`, or new fast-path decoders) that omits the "size must be `>= 0`" check reintroduces this exact class of DoS.

### Recommendation
- Confirm (via full read of `ArrayDecoders.java`) that every consumer of a wire-decoded `int` length used to drive a skip/read loop validates `size >= 0` (and `<= remaining bytes`) before use, and that this check precedes any loop-increment logic, not just precedes memory access.
- Add/keep the existing regression tests (`testException_skipField_infiniteLoop` and the negative-size variants) in the CI matrix with the enforced `timeout` to catch regressions.
- Apply the same audit to any other hand-rolled skip/dispatch loops across Java (`CodedInputStream`), C++ (`WireFormatLite::SkipField`), and `upb`, ensuring negative/overflowed lengths cannot be interpreted as valid iteration bounds.

### Proof of Concept
Java (`ArrayDecodersTest`), reproducing the historical hang scenario (now expected to throw rather than hang, per current test):
```java
// Byte 0: 0x0B (Start Group, field 1)
// Byte 1: 0x12 (Length Delimited, field 2)
// Byte 2-6: FA FF FF FF 0F  -> decodes to length = -6 as varint32
byte[] data = new byte[] {0x0B, 0x12, (byte) 0xFA, (byte) 0xFF, (byte) 0xFF, (byte) 0xFF, (byte) 0x0F};
ArrayDecoders.skipField(0x0B, data, 1, data.length, registers);
``` [1](#0-0) 

This is the concrete, in-repo evidence of the analog; I could not verify from the current session whether any other decode path (e.g., a newly added fast-path decoder) has regressed this check, since the full `ArrayDecoders.java` source was not retrievable in this pass. A Devin session with full file access would be needed to audit every length-consuming loop in that file line-by-line to rule out a live regression.

### Citations

**File:** java/core/src/test/java/com/google/protobuf/ArrayDecodersTest.java (L351-371)
```java
  @Test
  public void testException_skipField_negativeSize_standard() {
    assertThrows(
        InvalidProtocolBufferException.class,
        () ->
            ArrayDecoders.skipField(
                TAG,
                NEGATIVE_SIZE_0.toByteArray(),
                0,
                NEGATIVE_SIZE_0.size(),
                registers));
  }

  @Test
  public void testException_skipField_negativeSize_crafted() {
    // -6 encoded as 5-byte varint
    byte[] data = new byte[] {(byte) 0xFA, (byte) 0xFF, (byte) 0xFF, (byte) 0xFF, (byte) 0x0F};
    assertThrows(
        InvalidProtocolBufferException.class,
        () -> ArrayDecoders.skipField(TAG, data, 0, data.length, registers));
  }
```

**File:** java/core/src/test/java/com/google/protobuf/ArrayDecodersTest.java (L373-384)
```java
  @Test(timeout = 1000)
  public void testException_skipField_infiniteLoop() {
    // Payload that triggers the infinite loop:
    // Byte 0: 0x0B (Start Group 1)
    // Byte 1: 0x12 (Length Delimited 2)
    // Byte 2-6: FA FF FF FF 0F (Length -6)
    byte[] data =
        new byte[] {0x0B, 0x12, (byte) 0xFA, (byte) 0xFF, (byte) 0xFF, (byte) 0xFF, (byte) 0x0F};
    assertThrows(
        InvalidProtocolBufferException.class,
        () -> ArrayDecoders.skipField(0x0B, data, 1, data.length, registers));
  }
```
