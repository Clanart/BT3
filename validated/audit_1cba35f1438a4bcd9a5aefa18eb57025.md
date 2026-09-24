Confirmed: `isCorrupted()` in `LazyFieldLite.java:508-510` is defined but has zero callers anywhere else in the codebase — the corruption flag it sets is never consulted by any generated code path, so nothing downstream can distinguish "field was legitimately empty" from "field bytes were malformed and silently discarded." This confirms the analog: the internal signal exists but is dead code, exactly mirroring `set_policy` never checking `verify_from`'s (would-be) success signal.

### Title
Lazy extension field silently discards corrupted bytes and reports success on merge — ([File: java/core/src/main/java/com/google/protobuf/LazyFieldLite.java])

### Summary
`LazyFieldLite`/`InternalLazyField` implement lazy (on-demand) parsing for proto2 lazy message fields and `MessageSet` extensions in the Java runtime. When the delayed bytes turn out to be malformed at first-access time, `ensureInitialized()` and `mergeFrom()`/`mergeValueAndBytes()` catch `InvalidProtocolBufferException`, silently replace the field's value with the default instance (and discard the original bytes), and return normally as if parsing succeeded. This is the direct analog of nucypher's `verify_from` returning a "successful" decrypted value even when signature verification failed, and of `set_policy` never checking that success signal.

### Finding Description
`LazyFieldLite.ensureInitialized(MessageLite defaultInstance)` [1](#0-0)  parses `delayedBytes` on first access. If `parseFrom` throws `InvalidProtocolBufferException`, the code sets `corrupted = true`, but then unconditionally sets `this.value = defaultInstance` and `this.memoizedBytes = ByteString.EMPTY`, discarding the original bytes and continuing as if nothing went wrong — the comment even says "Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto was invalid."

The same silent-swallow pattern recurs in `mergeFrom(CodedInputStream, ExtensionRegistryLite)` [2](#0-1)  and in the static helper `mergeValueAndBytes` [3](#0-2) , both of which catch the same exception and fall back to the pre-merge value with no error propagated to the caller.

Although a `corrupted` flag is set and exposed via the package-private `isCorrupted()` accessor [4](#0-3) , that method has no callers anywhere in the generated-message or schema code (`ExtensionSchemaLite`, `MessageSetSchema`, `GeneratedMessageLite`, `ArrayDecoders`) — it is dead code. This is structurally identical to the reported bug class: a verification/validity signal is computed but never consulted by the caller that decides what to do with the (now-implicitly-trusted) result.

`InternalLazyField.ensureInitialized()` in the newer implementation has the same shape: it sets `corrupted = true` and *rethrows* on first parse, but the corruption is only guarded against re-parsing attempts, not surfaced through the message's public accessor API to the caller who originally read the field's value via `getValue()` after a successful, non-throwing merge path [5](#0-4) .

### Impact Explanation
An attacker who can supply a bounded proto2 message containing a lazy extension or `MessageSet` entry with intentionally malformed inner bytes (a `LAZY` proto2 field, or a `google.protobuf.MessageSet` item — both trusted, ordinary schema features, not attacker-controlled schema) causes the receiving application to silently observe that extension/field as "default/absent" instead of getting a hard parse failure. Because the outer message's `mergeFrom`/`parseFrom` returns success (no `InvalidProtocolBufferException` escapes for the corrupted sub-field), the application logic that would normally reject malformed input on `parseFrom` failure never runs, and the message is treated as valid with data silently missing. This is an integrity failure of the exact kind in the report: attacker-modified data is accepted and used as if verification/parsing succeeded, and no code path exists to detect it since the `corrupted` flag is unused. Depending on how the application uses that extension field (e.g., authorization/config data carried in a lazy extension), this can lead to silent loss of intended state — analogous to nucypher's policy arrangement becoming permanently unusable because the corrupted update was accepted without being flagged.

### Likelihood Explanation
Reachable via the fully public, supported binary-parsing surface (`parseFrom`/`mergeFrom` on any `GeneratedMessageLite` with a `[lazy = true]` proto2 extension, or any `MessageSet`) using only a bounded, malformed payload — no privileged access, no hostile schema, no huge input required. The only precondition is that the target message's `.proto` declares a lazy field/extension or uses `MessageSet`, which is a normal, trusted, and common schema feature.

### Recommendation
- Propagate the corruption instead of silently substituting the default instance: throw (or record on the outer message an unrecoverable-parse state) so `parseFrom`/`mergeFrom` on the enclosing message fails, matching normal (non-lazy) field parsing behavior.
- Wire the already-computed `corrupted` flag (`LazyFieldLite.isCorrupted()`) into `ExtensionSchemaLite`/`MessageSetSchema`/`GeneratedMessageLite` so at least one caller consults it, or remove the dead flag and replace the silent-catch with a rethrow.
- Apply the same fix to `mergeFrom(CodedInputStream, ...)` and `mergeValueAndBytes` so merges of corrupted lazy bytes are not silently treated as no-ops.

### Proof of Concept
1. Define a proto2 message with a lazy message-type extension/field, e.g. `optional TestMessage lazy_field = 1 [lazy = true];`, or use any `MessageSet` extension.
2. Construct the outer message manually so that the lazy field's inner length-delimited bytes are well-formed at the wire-tag level (so the *outer* parse succeeds) but contain invalid content for the inner message type (e.g., truncated/garbage bytes after a valid length prefix).
3. Call `OuterMessage.parseFrom(bytes)` — this returns successfully with no exception.
4. Call `getLazyField()` (or the extension getter) — instead of an `InvalidProtocolBufferException`, the accessor silently returns `TestMessage.getDefaultInstance()`, and `LazyFieldLite.isCorrupted()` (inaccessible to the caller, and unused internally) is the only place the failure is recorded — confirmed by `ensureInitialized()` at `java/core/src/main/java/com/google/protobuf/LazyFieldLite.java:469-496` and the absence of any caller of `isCorrupted()` in the codebase.

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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L507-510)
```java
  /** Returns whether the lazy field was corrupted and replaced with an empty message. */
  boolean isCorrupted() {
    return corrupted;
  }
```

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L197-228)
```java
  /**
   * Guarantees that `this.value` is non-null or throws.
   *
   * @throws InvalidProtocolBufferException If `bytes` cannot be parsed.
   */
  private void ensureInitialized() throws InvalidProtocolBufferException {
    if (value != null) {
      return;
    }

    synchronized (this) {
      if (corrupted) {
        throw new InvalidProtocolBufferException("Repeat access to corrupted lazy field");
      }
      try {
        // `bytes` is guaranteed to be non-null since `value` was null.
        CodedInputStream input = bytes.newCodedInput();
        input.enableAliasing(/* enabled= */ true);
        // When lazyExtensionEnabled() returns true, it means all extensions including MessageSet's
        // will be fully parsed. When it returns false, it basically implies this can only be a
        // MessageSet extension, and we should fall back to the old behavior of silently returning
        // the default instance on corrupted extensions i.e. a full parse.
        value =
            extensionRegistry.lazyExtensionEnabled()
                ? defaultInstance.getParserForType().parsePartialFrom(input, extensionRegistry)
                : defaultInstance.getParserForType().parseFrom(input, extensionRegistry);
        input.checkLastTagWas(0);
      } catch (InvalidProtocolBufferException e) {
        corrupted = true;
        throw e;
      }
    }
```
