### Title
Silent swallowing of `InvalidProtocolBufferException` during lazy-field extension merge causes silent message corruption / data loss - ([File: java/core/src/main/java/com/google/protobuf/LazyFieldLite.java])

### Summary
The external report's core failed invariant is: a function that can fail on attacker-influenced input (`transferFrom` reverting/returning false for non-standard tokens) has its failure signal discarded by the caller, so the caller believes the operation succeeded while state is actually inconsistent (tokens stuck, no accounting). The Protobuf analog is in `LazyFieldLite`, the internal helper backing lazily-parsed message-type extensions/`Any`-like fields in Java Lite/full runtimes reachable from the public `parseFrom`/`mergeFrom` APIs. When merging an already-parsed lazy field with new delayed bytes from attacker-controlled wire input, a parse failure (`InvalidProtocolBufferException`) is caught and discarded without surfacing an error to the caller, silently replacing the field with an empty/stale value instead of failing the parse.

### Finding Description
`LazyFieldLite.mergeFrom(CodedInputStream, ExtensionRegistryLite)` and the private helper `mergeValueAndBytes` both call into `.mergeFrom(...)` on an already-parsed sub-message, and on failure do this: [1](#0-0) 
```
    try {
      setValue(value.toBuilder().mergeFrom(input, extensionRegistry).build());
    } catch (InvalidProtocolBufferException e) {
      // Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto
      // was invalid.
    }
```
and similarly: [2](#0-1) 

The same pattern exists in the lazy-initialization path `ensureInitialized`, which is invoked the first time any getter touches a lazily-parsed extension/message field: [3](#0-2) 

Here, malformed bytes from an untrusted wire payload (attacker-controlled `delayedBytes`) cause the catch block to silently drop the error, mark the field `corrupted`, and substitute the type's *default instance* for the real (partially-parsed) value — with no exception propagated to the caller of `parseFrom`/`mergeFrom`.

**Invariant transfer:** Just as the ERC20 analog assumes `transferFrom` failure must be surfaced (via `safeTransferFrom`) so the caller's accounting stays consistent, Protobuf's public parsing contract promises `InvalidProtocolBufferException` on malformed input so callers can reject bad data. Here that guarantee is deliberately bypassed for a subset of lazily-parsed fields: the exception is caught internally and never rethrown, so the top-level `parseFrom`/`mergeFrom` call returns *successfully* even though part of the message failed to parse. The consuming application, exactly analogous to the vault's "profit was received" assumption, will trust that the returned message correctly reflects the wire bytes, when in fact a nested extension/message field silently reverted to its default (empty) value.

**Missing check:** No `isCorrupted()` check or error propagation exists at the call sites that would prevent normal Message/Builder consumers from observing this corrupted state through ordinary getters; `isCorrupted()` is package-private and used mainly for internal testing/telemetry, not exposed to application logic.

### Impact Explanation
This breaks the integrity invariant that a successfully-returned parsed message accurately represents the input bytes. For applications that rely on protobuf's "either parse succeeds and the message is faithful, or it throws" contract (e.g., for authorization data embedded in extension fields, MessageSet-style extensions, or nested `Any`-like payloads), an attacker who controls the serialized bytes of a lazily-parsed sub-field can cause that field to silently disappear/reset to default, while the overall parse call reports success. Depending on how the consuming application uses that field (e.g., a permission flag, a signed payload, an amount), this can produce broken business-logic assumptions similar to "funds silently lost" in the ERC20 analog — though the direct consequence here is data integrity/availability of a specific field rather than an attacker directly manipulating the corrupted value to their advantage (they can only make it revert to default). This is comparable to a Medium-severity integrity issue: reachable via the public parse API with a crafted, bounded payload, no crash or memory-safety consequence, but a real violation of the "malformed data ⇒ exception" parsing contract for a subset of message fields.

### Likelihood Explanation
Likelihood is bounded by the fact that this code path is specific to `LazyFieldLite`, used for the "MessageSet"/extension lazy-parsing mechanism, not the mainstream generated-code parse path for ordinary fields. To trigger it, an attacker needs the target message type to use lazily-parsed extension fields (`lazy = true` proto option) that a schema/application already trusts, and craft the corresponding extension bytes to be malformed while the rest of the message parses fine, or perform a merge where already-parsed content is merged with newly-malformed bytes. This is entirely within the "ordinary client sends bounded binary Protobuf through a public parse API with a trusted schema" threat model, so it is reachable, but the actual security-relevant impact depends heavily on how the consuming application uses that specific lazily-parsed field.

### Recommendation
Do not silently swallow `InvalidProtocolBufferException` in `LazyFieldLite.mergeFrom`, `mergeValueAndBytes`, and `ensureInitialized`. At minimum, propagate the failure to the caller of the top-level `parseFrom`/`mergeFrom` (e.g., rethrow a wrapped `InvalidProtocolBufferException`, or expose `isCorrupted()` publicly and have generated-code parse entry points check it and fail the overall parse). This restores the "malformed bytes ⇒ parse failure" invariant instead of allowing lazily-parsed sub-fields to silently revert to default values while the outer parse reports success.

### Proof of Concept
A concrete runnable reproduction requires access to the generated-code path that constructs `LazyFieldLite` for a `lazy = true` extension field and driving two sequential `mergeFrom` calls where the first succeeds (so `value` is non-null) and the second supplies bytes that fail to parse as that extension's message type. I was not able to fully trace the generated-code call sites that instantiate `LazyFieldLite` for such fields within the available indexed context (searches for `MessageSetSchema`/`SchemaUtil` usage of `LazyFieldLite`/`isCorrupted` returned no matches in this checkout, likely due to index coverage limits), so I cannot provide a verified end-to-end trace confirming which generated schema classes route lazy extension parsing through this exact code. The unit test file `java/core/src/test/java/com/google/protobuf/LazyFieldLiteTest.java` (48 matches for `LazyFieldLite`/`isCorrupted`) is the best starting point to construct and run a concrete PoC exercising the swallowed-exception paths at lines 357-366, 368-377, and 468-496 shown above. Given the index limits, a Devin session with full repository access is recommended to trace the exact generated-code call sites and execute a definitive PoC before treating this as fully confirmed.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L357-366)
```java
    // We are parsed and both contain data. We won't drop any extensions here directly, but in the
    // case that the extension registries are not the same then we might in the future if we
    // need to serialize and parse a message again.
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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L468-496)
```java
  /** Might lazily parse the bytes that were previously passed in. Is thread-safe. */
  protected void ensureInitialized(MessageLite defaultInstance) {
    if (value != null) {
      return;
    }
    synchronized (this) {
      if (value != null) {
        return;
      }
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
  }
```
