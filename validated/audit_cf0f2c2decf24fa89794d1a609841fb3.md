### Title
Integer overflow in packed-field `endPos` computation can silently truncate/corrupt parsing of packed repeated fields - ([File: java/core/src/main/java/com/google/protobuf/CodedInputStreamReader.java])

### Summary
The Teku report fixes an unbounded `weight` addition that could overflow `uint64` because no upper-bound check existed before the addition, causing wraparound and incorrect downstream calculations (fixed with `safePlus`/`safeMax` clamps). The transferable invariant is: **any attacker-controlled length/size value that is added to a running position/counter without an overflow check can wrap around and silently corrupt parsing/accounting logic.** In `CodedInputStreamReader`, the packed-field readers compute `int endPos = input.getTotalBytesRead() + bytes;` where `bytes` comes directly from `input.readUInt32()` — an attacker-controlled 32-bit value that can be up to `0xFFFFFFFF` (represented as a negative Java `int`). This addition is not overflow-checked. For varint-typed packed fields (`readUInt64List`, `readInt64List`, `readUInt32List`, etc.) there is **no subsequent bound/sanity validation of `bytes`** analogous to `verifyPackedFixed32Length`/`verifyPackedFixed64Length` used only for fixed-width packed lists.

### Finding Description
In `CodedInputStreamReader.java`, packed-field parsing repeatedly follows this pattern:
```java
case WIRETYPE_LENGTH_DELIMITED:
  final int bytes = input.readUInt32();
  int endPos = input.getTotalBytesRead() + bytes;
  while (input.getTotalBytesRead() < endPos) {
    target.add(input.readUInt64()); // or readInt64/readUInt32/etc.
  }
  requirePosition(endPos);
``` [1](#0-0) [2](#0-1) [3](#0-2) 

`bytes` is `input.readUInt32()`, a raw 32-bit varint field length that is fully attacker-controlled and can encode any 32-bit pattern, including values that overflow when added to the current stream position — since `getTotalBytesRead()` returns a Java `int`, and Java `int` addition silently wraps on overflow. Unlike the fixed-width packed readers (`readDoubleList`, `readSFixed32List`, `readSFixed64List`), which call `verifyPackedFixed32Length(bytes)` / `verifyPackedFixed64Length(bytes)` before computing `endPos`, the variable-width varint packed list readers (`readUInt64List`, `readInt64List`, `readUInt32List`, `readInt32List`, `readSInt32List`, `readSInt64List`, `readBoolList`, `readEnumList`) perform **no bound check on `bytes`** at all before the addition: [4](#0-3) 

The general `CodedInputStream` overflow guard `isBeyondLimit` exists elsewhere in the codebase for the low-level buffer-refill path, using an overflow-conscious comparison: [5](#0-4) 
but this guard is only applied inside `tryRefillBuffer`, not in the `CodedInputStreamReader` packed-list `endPos` computation, so the `int endPos = getTotalBytesRead() + bytes` line itself is not protected by it before the loop condition is evaluated.

### Impact Explanation
If `bytes` is large enough (or negative when reinterpreted, since `readUInt32()` returns a signed `int`), `endPos` can wrap to a value smaller than the current `getTotalBytesRead()`. This causes the `while (getTotalBytesRead() < endPos)` loop to never execute, so the packed field is silently parsed as empty instead of throwing `InvalidProtocolBufferException`. This is a parsing-correctness/integrity issue: a malformed or crafted packed field with a length that triggers overflow yields silently truncated data (zero elements) rather than a rejected message, which could cause a consuming application relying on protobuf's "either fully parses or throws" contract to process an incomplete/incorrect message as if it were valid — directly analogous to Teku's unmitigated wraparound causing incorrect (but non-crashing) computed values. It is not a memory-safety bug (no buffer over-read/write is directly evidenced) and does not, on the current evidence, cause a crash, so severity is bounded to a data-integrity/parsing-differential issue rather than memory corruption.

### Likelihood Explanation
Reaching this code requires only a bounded, well-formed protobuf message with one packed repeated field (`int32`/`int64`/`uint32`/`uint64`/`sint*`/`bool`/`enum`) whose length prefix is a crafted varint near `2^31`–`2^32`, an easily attacker-constructed input via the public `parseFrom` API — no privileged access needed. This satisfies the "ordinary client sending bounded binary Protobuf" threat model. However, I was unable to fully trace `requirePosition()`'s exact implementation in this checkout (search for its definition did not return file contents beyond the class body I retrieved), so I cannot confirm with certainty whether `requirePosition(endPos)` independently catches this specific wraparound case downstream and throws before any incorrect field is committed to the target list. This uncertainty means the exploitability of "silent truncation vs. exception" could not be fully confirmed via static reading alone in this session.

### Recommendation
Add an overflow-checked computation for `endPos` in all packed-field readers in `CodedInputStreamReader.java` (both fixed-width and varint-width cases), mirroring `CodedInputStream.isBeyondLimit`'s overflow-conscious approach, e.g. reject the field immediately with `InvalidProtocolBufferException` if `bytes < 0` or if `getTotalBytesRead() + bytes` would exceed `Integer.MAX_VALUE` or the current stream/size limit, rather than relying on unmitigated `int` addition. Apply the same validation currently limited to `verifyPackedFixed32Length`/`verifyPackedFixed64Length` uniformly to varint packed-list paths.

### Proof of Concept
I could not execute a live test in this session (no runtime access), so I cannot claim a verified reproduction ran. A conceptual PoC: construct a protobuf message with a packed `repeated uint64` field whose wiretype is `WIRETYPE_LENGTH_DELIMITED` and whose length varint encodes a value such that `getTotalBytesRead() + bytes` overflows a 32-bit signed `int` (e.g., `bytes = 0x7FFFFFFF` while `getTotalBytesRead()` is already non-trivial, or `bytes` encoding a negative int like `0xFFFFFFFF`). Parse this via `CodedInputStreamReader.readUInt64List` and observe whether the loop is skipped/`endPos` is negative and whether `requirePosition` fails to raise an exception — this would need to be confirmed by running the existing test harness (e.g., extending `CodedInputStreamTest`, which already contains overflow-focused tests such as `testSkipRawBytesSizeLimit`) against the packed-list code path specifically, which was not done here. [6](#0-5)

### Citations

**File:** java/core/src/main/java/com/google/protobuf/CodedInputStreamReader.java (L376-386)
```java
  public void readUInt64List(List<Long> target) throws IOException {
    if (target instanceof LongArrayList) {
      LongArrayList plist = (LongArrayList) target;
      switch (WireFormat.getTagWireType(tag)) {
        case WIRETYPE_LENGTH_DELIMITED:
          final int bytes = input.readUInt32();
          int endPos = input.getTotalBytesRead() + bytes;
          while (input.getTotalBytesRead() < endPos) {
            plist.addLong(input.readUInt64());
          }
          requirePosition(endPos);
```

**File:** java/core/src/main/java/com/google/protobuf/CodedInputStreamReader.java (L433-443)
```java
  public void readInt64List(List<Long> target) throws IOException {
    if (target instanceof LongArrayList) {
      LongArrayList plist = (LongArrayList) target;
      switch (WireFormat.getTagWireType(tag)) {
        case WIRETYPE_LENGTH_DELIMITED:
          final int bytes = input.readUInt32();
          int endPos = input.getTotalBytesRead() + bytes;
          while (input.getTotalBytesRead() < endPos) {
            plist.addLong(input.readInt64());
          }
          requirePosition(endPos);
```

**File:** java/core/src/main/java/com/google/protobuf/CodedInputStreamReader.java (L844-854)
```java
  public void readUInt32List(List<Integer> target) throws IOException {
    if (target instanceof IntArrayList) {
      IntArrayList plist = (IntArrayList) target;
      switch (WireFormat.getTagWireType(tag)) {
        case WIRETYPE_LENGTH_DELIMITED:
          final int bytes = input.readUInt32();
          int endPos = input.getTotalBytesRead() + bytes;
          while (input.getTotalBytesRead() < endPos) {
            plist.addInt(input.readUInt32());
          }
          requirePosition(endPos);
```

**File:** java/core/src/main/java/com/google/protobuf/CodedInputStreamReader.java (L958-965)
```java
  public void readSFixed32List(List<Integer> target) throws IOException {
    if (target instanceof IntArrayList) {
      IntArrayList plist = (IntArrayList) target;
      switch (WireFormat.getTagWireType(tag)) {
        case WIRETYPE_LENGTH_DELIMITED:
          final int bytes = input.readUInt32();
          verifyPackedFixed32Length(bytes);
          int endPos = input.getTotalBytesRead() + bytes;
```

**File:** java/core/src/main/java/com/google/protobuf/CodedInputStream.java (L2666-2674)
```java
    /**
     * Helper to perform an integer-overflow-conscious check that {@code currentOffset + bytesToAdd}
     * does not exceed {@code limit}.
     *
     * <p>Assumes that {@code currentOffset >= 0}, {@code bytesToAdd >= 0}, and {@code limit >= 0}.
     */
    private static boolean isBeyondLimit(int currentOffset, int bytesToAdd, int limit) {
      return limit < currentOffset || bytesToAdd > limit - currentOffset;
    }
```

**File:** java/core/src/test/java/com/google/protobuf/CodedInputStreamTest.java (L560-619)
```java
  @Test
  public void testSkipRawBytesSizeLimit() throws Exception {
    InputStream input =
        new InputStream() {
          private long remaining = 5L * Integer.MAX_VALUE; // 10GB

          @Override
          public int read() throws IOException {
            if (remaining <= 0) {
              return -1;
            }
            remaining--;
            return 0;
          }

          @Override
          public int read(byte[] b, int off, int len) throws IOException {
            if (remaining <= 0) {
              return -1;
            }
            int toRead = (int) Math.min(len, remaining);
            Arrays.fill(b, off, off + toRead, (byte) 0);
            remaining -= toRead;
            return toRead;
          }

          @Override
          public long skip(long n) throws IOException {
            long toSkip = Math.min(n, remaining);
            remaining -= toSkip;
            return toSkip;
          }

          @Override
          public int available() {
            return remaining > Integer.MAX_VALUE ? Integer.MAX_VALUE : (int) remaining;
          }
        };

    CodedInputStream stream = CodedInputStream.newInstance(input);
    stream.setSizeLimit(100);

    // Skip 50 bytes (should succeed)
    stream.skipRawBytes(50);

    // Attempting to skip beyond the size limit should throw sizeLimitExceeded.
    // We try to skip 100 bytes more (total 150, exceeds sizeLimit 100)
    assertThrows(InvalidProtocolBufferException.class, () -> stream.skipRawBytes(100));

    // Also try to skip a huge amount that would overflow the old naive check.
    // In old code, this would succeed and allow further reads.
    CodedInputStream stream2 = CodedInputStream.newInstance(input);
    stream2.setSizeLimit(Integer.MAX_VALUE);
    stream2.skipRawBytes(Integer.MAX_VALUE - 50); // should succeed

    // This second skip will overflow A + size in buggy code, but in fixed code it should throw.
    assertThrows(
        InvalidProtocolBufferException.class, () -> stream2.skipRawBytes(Integer.MAX_VALUE));
  }

```
