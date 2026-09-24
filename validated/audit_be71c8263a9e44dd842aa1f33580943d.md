### Title
Silent swallowing of `InvalidProtocolBufferException` in `LazyFieldLite` merge/parse paths causes corrupted sub-messages to be treated as valid defaults - ([File: java/core/src/main/java/com/google/protobuf/LazyFieldLite.java])

### Summary
`LazyFieldLite`, which backs Java `[lazy = true]` message fields (and is used generically for delayed parsing of embedded/extension sub-messages), catches `InvalidProtocolBufferException` in three separate code paths and discards it without ever signaling failure to the caller. This mirrors the ERC20 report's core invariant violation: a fallible operation ("transfer" there, "parse/merge" here) can fail, but the failure is never observed by the caller, which proceeds as if the operation succeeded — silently corrupting downstream state.

### Finding Description
Three sites in `LazyFieldLite` explicitly comment on the intentional swallowing of failure: [1](#0-0) [2](#0-1) [3](#0-2) 

In `mergeFrom(CodedInputStream, ExtensionRegistryLite)`, when the field is already parsed (`value != null`) and new bytes arrive from the wire, the code attempts `value.toBuilder().mergeFrom(input, extensionRegistry).build()`. If that throws `InvalidProtocolBufferException` (malformed/truncated bytes from an attacker-controlled input stream), the exception is caught and **dropped**, leaving `value` at its pre-merge state and never informing the caller that some of the incoming wire data was invalid or lost.

`mergeValueAndBytes` (used by `merge(LazyFieldLite)`, e.g. when combining two messages during a `MergeFrom(Message)` call, such as repeated occurrence of the same lazily-parsed field in a stream) has the identical pattern: it swallows the exception and falls back to returning the untouched prior `value`, silently discarding the newly parsed bytes.

`ensureInitialized`, which lazily parses `delayedBytes` on first access (triggered by any `getValue()`/getter call on the lazy field from a fully public code path), also catches `InvalidProtocolBufferException`, sets an internal `corrupted` flag, and substitutes the default instance — again with no exception propagated to the caller.

The `corrupted` boolean is only exposed via a package-private `isCorrupted()` accessor; based on the codebase search, it is only referenced within `LazyFieldLite` itself and its unit test, not consulted by the generated message merge/serialize code paths, meaning ordinary API consumers (`Message.parseFrom`, `Builder.mergeFrom`, `getExtension`, etc.) have no way to detect that a lazily-parsed field silently failed to parse.

This transfers the ERC20 report's invariant directly: the "transfer" (data merge) can return failure through an exception, but exception handling here behaves like an unchecked boolean return — it is caught and thrown away, and the calling code (a generated `Message.Builder`) has no branch to detect or react to it.

### Impact Explanation
An attacker who controls a Protobuf message containing a `[lazy = true]` sub-message field (or triggers a merge of two messages containing the same field, one of which carries corrupted/truncated bytes for that field) can cause the parser to silently discard the corrupted payload and substitute a default instance, or silently drop a legitimate second occurrence of the field during merge, without the caller (application code that only checks for `IOException`/`InvalidProtocolBufferException` from the top-level `parseFrom` call) ever seeing an error. Because the top-level parse can complete "successfully" while an inner sub-message is quietly reset to defaults, this is a data-integrity issue: application logic that trusts a successfully-returned message may operate on a semantically different message than what was actually sent (e.g., a nested authorization/config payload silently reverting to defaults), which is analogous to a transfer silently failing while the caller believes it succeeded. This is a legitimate binary-parsing-time analog reachable purely through the public `Message.parseFrom`/`mergeFrom` API with a bounded, attacker-crafted payload — no privileged access or hostile schema required.

### Likelihood Explanation
Reaching this code requires: (1) a proto schema using a `[lazy = true]` field (an application design choice, but a supported and legitimate Protobuf feature), and (2) supplying bytes for that field that parse successfully at the outer message level but are invalid for the inner lazy sub-message, or triggering a `mergeFrom` where the field appears twice with one occurrence corrupted. Both are achievable by an ordinary client sending bounded, malformed-but-valid-length binary protobuf through the standard `parseFrom` entry point — no need for huge payloads, deep recursion, or resource exhaustion. Likelihood is Medium: it depends on the target application actually using lazy fields, but when it does, the trigger is trivial to construct.

### Recommendation
- Do not silently discard `InvalidProtocolBufferException` in `LazyFieldLite.mergeFrom`, `mergeValueAndBytes`, and `ensureInitialized`. At minimum, propagate the `corrupted` state through the public message API (e.g., surface it via `isInitialized()`/parsing result, or rethrow when the outer message is being parsed rather than merged from an already-mutable builder).
- Where silent recovery-to-default is intentional (to preserve best-effort merge semantics for lazy fields), document this loudly in the public API surface (`GeneratedMessageLite`/`Message` Javadoc) so integrators are aware that a successful `parseFrom`/`mergeFrom` does not guarantee sub-message integrity for lazy fields, and provide a supported way (e.g. `isCorrupted()` made public, or a validation pass) for callers to detect this condition instead of relying on internal package-private state that generated code does not check.
- Add regression tests asserting that corruption in a lazily-parsed sub-field is detectable by the caller through some public signal.

### Proof of Concept
Conceptual reproduction using trusted schema and bounded input (cannot be executed in this environment, but the code paths make the behavior deterministic):
1. Define a proto message `Outer` with a field `inner` of message type `Inner`, annotated `[lazy = true]`, and one `Inner` field that is itself a nested message type.
2. Serialize a valid `Outer` with `inner` set to a legitimate `Inner` value; call `outer.toBuilder()` to get a builder with `value` already populated (parsed) for the lazy field.
3. Construct a second wire fragment for the same field number containing truncated/invalid length-delimited bytes for `Inner` (e.g., a length prefix greater than the remaining bytes).
4. Feed this second fragment into `builder.mergeFrom(CodedInputStream, extensionRegistry)` (or perform `Outer.newBuilder(first).mergeFrom(second)` where `second` carries the corrupt field).
5. Observe: `LazyFieldLite.mergeFrom` (lines 360–366) catches the resulting `InvalidProtocolBufferException` and returns normally; the outer `mergeFrom` call returns success; `builder.build().getInner()` returns the original (unmerged) `Inner` value, and no exception or corruption flag is surfaced to the calling code — despite the wire input for that field having been invalid.

Because this analysis was performed via the code index and not a live checkout, exact behavior at the generated-code call sites (`GeneratedMessageLite` field accessors for lazy fields) was inferred from `LazyFieldLite.java` and `SchemaUtil.java` references; a background Devin session with full repository/build access would be needed to compile and execute the above PoC end-to-end and confirm the exact exception-swallowing behavior at runtime.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L360-366)
```java
    try {
      setValue(value.toBuilder().mergeFrom(input, extensionRegistry).build());
    } catch (InvalidProtocolBufferException e) {
      // Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto
      // was invalid.
    }
  }
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L368-377)
```java
  private static MessageLite mergeValueAndBytes(
      MessageLite value, ByteString otherBytes, ExtensionRegistryLite extensionRegistry) {
    try {
      return value.toBuilder().mergeFrom(otherBytes, extensionRegistry).build();
    } catch (InvalidProtocolBufferException e) {
      // Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto
      // was invalid.
      return value;
    }
  }
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L477-495)
```java
      try {
        if (delayedBytes != null) {
          // The extensionRegistry shouldn't be null here since we have delayedBytes.
          MessageLite parsedValue =
              defaultInstance.getParserForType().parseFrom(delayedBytes, extensionRegistry);
          this.value = parsedValue;
          this.memoizedBytes = delayedBytes;
        } else {
          this.value = defaultInstance;
          this.memoizedBytes = ByteString.EMPTY;
        }
      } catch (InvalidProtocolBufferException e) {
        // Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto
        // was invalid.
        this.corrupted = true;
        this.value = defaultInstance;
        this.memoizedBytes = ByteString.EMPTY;
      }
    }
```
