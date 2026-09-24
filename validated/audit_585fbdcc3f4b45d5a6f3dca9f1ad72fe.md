### Title
Packed extension enum field with unrecognized value skips `popLimit`, corrupting `CodedInputStream` limit state - (File: `java/core/src/main/java/com/google/protobuf/GeneratedMessageLite.java`)

### Summary
The ImageMagick CVE is a missing-cleanup-on-error-path bug: a resource (memory) is allocated while parsing a field, an error/edge condition is hit, and the function returns without releasing/restoring that resource, leaking state for the life of the process. The closest protobuf analog is a missing cleanup on an early-return branch in the packed-extension-parsing code, but instead of leaking heap memory it leaks parser *limit state* on the shared `CodedInputStream`.

### Finding Description
`parseExtension` in `ExtendableMessage.parseExtension` handles packed repeated extension fields by pushing a length limit before the loop and popping it after: [1](#0-0) 

For packed **enum** extensions specifically, when an unrecognized enum value is encountered inside the loop, the code does: [2](#0-1) 

`return true;` is executed directly from inside the `while (input.getBytesUntilLimit() > 0)` loop, **before** `input.popLimit(limit)` on line 733 is reached. This is the same bug class as `ReadMATImage`: a value is pushed/allocated (`pushLimit`), a per-item validation fails (unrecognized enum, analogous to a malformed chunk), and the function bails out on that specific error branch without releasing/restoring the resource that was set up for the duration of the loop.

`CodedInputStream.pushLimit`/`popLimit` implement a save/restore protocol: `pushLimit` narrows `currentLimit` to the end of the packed sub-field and returns the *previous* (outer) limit so the caller can restore it via `popLimit`. If `popLimit` is skipped, the `CodedInputStream`'s `currentLimit` permanently remains pinned to the end of the packed field's sub-range instead of being restored to the limit of the enclosing message/stream.

### Impact Explanation
Because `CodedInputStream` instances are reused across the entire message (and, via `parseDelimitedFrom` sequences, potentially across multiple top-level messages read from the same stream/socket), leaving `currentLimit` un-restored means:
- Every subsequent read on that stream believes it has reached "end of message" once it hits the stale, narrower limit, even though more legitimate bytes remain in the underlying buffer — causing later fields in the same message (or subsequent messages read from the same stream) to be silently truncated/dropped, which is an integrity/data-loss issue rather than a crash.
- Alternatively, a later legitimate `pushLimit` call, which asserts the new limit doesn't exceed the current outer limit, can throw `InvalidProtocolBufferException` for otherwise well-formed data, turning a single malformed extension value from an untrusted peer into a denial-of-parsing condition for unrelated, valid data sharing the same stream.

This satisfies the "trusted schema, attacker-controlled bytes, missing check/cleanup on an error branch, and reachable corruption of parser state" pattern required by the prompt, and is a Lite-runtime binary-parsing code path reachable via the public `parseFrom`/`mergeFrom` extension-parsing API, not a test/mock/benchmark artifact.

### Likelihood Explanation
Any attacker controlling the bytes of a serialized message can trigger this: they only need one packed repeated enum **extension** field whose registry is known/trusted (extension registries are trusted per the prompt's assumptions) and include a single out-of-range enum value inside the packed blob, followed by additional bytes in the outer message or a subsequent delimited message on the same stream. No recursion, huge allocation, or privileged access is required — it's a straightforward wire-format edge case in a commonly used API path (`GeneratedMessageLite`/Lite runtime extension parsing).

### Recommendation
Restore the outer limit before the early return, e.g.:
```java
if (value == null) {
  input.popLimit(limit);
  return true;
}
```
or restructure the loop to `break` instead of `return`, falling through to the existing `input.popLimit(limit)` call on line 733.

### Proof of Concept
I was not able to complete a full field/line-level walk of `CodedInputStream.pushLimit`/`popLimit` in this session (ran out of tool iterations before reading `CodedInputStream.java`'s implementation), so the exact downstream failure mode (silent truncation vs. thrown `InvalidProtocolBufferException` on the next `pushLimit`) is inferred from the documented push/pop-limit contract rather than confirmed by tracing every call site. A concrete repro would be: construct a Lite message with a packed repeated enum extension field registered in an `ExtensionRegistryLite`, serialize a packed payload containing one invalid enum tag followed by valid trailing bytes for another field of the same message (or write a second delimited message on the same output stream), then call `SomeMessage.parseFrom(...)`/`parseDelimitedFrom` reusing one `CodedInputStream` and assert that the trailing field/second message is dropped or throws unexpectedly. I recommend a Devin session with terminal/build access to write and run this Java repro (compile against the Lite runtime, generate a test extension registry, and observe the parse result) to confirm the exact user-visible effect before filing upstream.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/GeneratedMessageLite.java (L710-733)
```java
      if (packed) {
        int length = input.readRawVarint32();
        int limit = input.pushLimit(length);
        if (extension.descriptor.getLiteType() == WireFormat.FieldType.ENUM) {
          while (input.getBytesUntilLimit() > 0) {
            int rawValue = input.readEnum();
            Object value = extension.descriptor.getEnumType().findValueByNumber(rawValue);
            if (value == null) {
              // If the number isn't recognized as a valid value for this
              // enum, drop it (don't even add it to unknownFields).
              return true;
            }
            extensions.addRepeatedField(
                extension.descriptor, extension.singularToFieldSetType(value));
          }
        } else {
          while (input.getBytesUntilLimit() > 0) {
            Object value =
                FieldSet.readPrimitiveField(
                    input, extension.descriptor.getLiteType(), /* checkUtf8= */ false);
            extensions.addRepeatedField(extension.descriptor, value);
          }
        }
        input.popLimit(limit);
```
