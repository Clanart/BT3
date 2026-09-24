Based on my investigation, I found a strong analog to CVE-2017-6470 in the Protobuf codebase.

### Title
Missing-length-progress infinite loop in group skipping - (File: `java/core/src/main/java/com/google/protobuf/ArrayDecoders.java`)

### Summary
The Wireshark CVE describes an IAX2 dissector loop that failed to constrain "packet lateness," letting a crafted/injected packet keep the loop from ever terminating. The Protobuf analog is in `ArrayDecoders.skipField`'s `WIRETYPE_START_GROUP` handling: the loop advances only by re-entering `skipField` recursively based on attacker-controlled tag/length varints, with no check that `position` is monotonically increasing past `limit` before the next iteration, similar to the pattern that a dedicated regression test (`ArrayDecodersTest.testException_skipField_infiniteLoop`) was written to catch [1](#0-0) .

### Finding Description
`ArrayDecoders.skipField` handles an unknown/unrecognized field of wire type `WIRETYPE_START_GROUP` by looping `while (position < limit)`, decoding a tag and then recursively skipping the corresponding field via `skipField(lastTag, data, position, limit, registers)` [1](#0-0) . Each iteration's forward progress depends entirely on the returned `position` from the recursive `skipField` call, which itself depends on `decodeVarint32`/`decodeVarint64`/`decodeLengthPrefixVarint` correctly detecting malformed varints (e.g., negative lengths encoded via a 5-byte varint with the sign bit set) and throwing `InvalidProtocolBufferException` rather than returning a `position` that doesn't advance past `limit`.

This mirrors the Wireshark IAX2 defect: an attacker-controlled length/lateness-like field is used to drive a loop's termination condition, and if the check that constrains that value is incomplete, the loop can fail to terminate (or add unbounded delay) on crafted input.

There is direct evidence in the repository that this exact class of defect was previously present and is now covered by a regression test: `testException_skipField_infiniteLoop` feeds a `START_GROUP` tag (`0x0B`) followed by a length-delimited field (`0x12`) whose length field is a 5-byte varint encoding `-6` (`FA FF FF FF 0F`), asserting that `skipField` throws `InvalidProtocolBufferException` within a 1-second timeout rather than looping forever [2](#0-1) . This test's presence, together with the sibling negative-size tests (`testException_skipField_negativeSize_standard`, `testException_skipField_negativeSize_crafted`) [3](#0-2) , indicates the upstream fix already constrains the "lateness" value (the negative/garbage length) by having `decodeLengthPrefixVarint` reject negative sizes and throw, which forces `skipField`'s length-delimited branch to fail fast instead of returning a bogus, non-advancing position that the enclosing `while (position < limit)` group loop would otherwise spin on.

I was not able to view the body of `decodeVarint32`, `decodeVarint64`, or `decodeLengthPrefixVarint` directly in this session (tool budget exhausted before retrieval succeeded), so I cannot fully confirm whether every code path that could return a non-advancing `position` is covered, or whether the fix is complete for all wire types nested inside a group (e.g., a deeply nested group containing another group with a crafted negative length at a different offset).

### Impact Explanation
If the loop failed to terminate (denial of service via infinite loop / hang), an ordinary client sending a bounded, crafted binary Protobuf message to any public parse API (`parseFrom`, `mergeFrom`) that routes through `MessageSchema`/`ArrayDecoders` could hang the parsing thread indefinitely, consuming CPU with a small, bounded input — directly analogous to the Wireshark IAX2 DoS via packet injection. This is a High-severity availability impact class, consistent with the CVSS 7.5 (`AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H`) of the original report, if reachable.

### Likelihood Explanation
The existence of a dedicated, timeout-bounded regression test (`testException_skipField_infiniteLoop`) strongly suggests this exact scenario was identified and fixed upstream — the test currently expects an exception, not a hang, meaning the current checked-out code appears to already reject the malformed-negative-length-in-group input that would have caused the infinite loop. Because I could not inspect `decodeLengthPrefixVarint`/`decodeVarint32` directly to confirm complete coverage of all negative-length and malformed-varint cases reachable through nested/START_GROUP skip paths, I cannot rule out a residual analog with high confidence, and I cannot fabricate a bypass without seeing that code.

### Recommendation
Given the strong indication that this exact defect class has already been patched and is guarded by a timeout-bounded unit test in this checkout, no actionable unpatched vulnerability can be confirmed here. A background agent with full read access should verify `decodeVarint32`, `decodeVarint64`, and `decodeLengthPrefixVarint` in `ArrayDecoders.java` to confirm all malformed-varint/negative-length paths always throw rather than returning a stale `position`, and should attempt to construct nested-group or extension-driven variants (e.g., malformed length inside a group inside a group, or inside `MessageSetSchema`'s group-based `mergeMessageSetExtension` loop) to rule out any remaining non-terminating path.

### Proof of Concept
No new reproducible infinite loop was found or run in this session. The existing repository test `ArrayDecodersTest.testException_skipField_infiniteLoop` [2](#0-1)  already encodes the minimal reproduction bytes (`0x0B 0x12 0xFA 0xFF 0xFF 0xFF 0x0F`) for this exact bug class and currently asserts it is fixed (throws rather than hangs), so I cannot claim a live, unpatched infinite loop without further code inspection that I was unable to complete.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/ArrayDecoders.java (L1080-1097)
```java
      case WireFormat.WIRETYPE_START_GROUP:
        final int endGroup = (tag & ~0x7) | WireFormat.WIRETYPE_END_GROUP;
        int lastTag = 0;
        registers.recursionDepth++;
        checkRecursionLimit(registers.recursionDepth);
        while (position < limit) {
          position = decodeVarint32(data, position, registers);
          lastTag = registers.int1;
          if (lastTag == endGroup) {
            break;
          }
          position = skipField(lastTag, data, position, limit, registers);
        }
        registers.recursionDepth--;
        if (position > limit || lastTag != endGroup) {
          throw InvalidProtocolBufferException.parseFailure();
        }
        return position;
```

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
