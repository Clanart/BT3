### Title
`corrupted` flag set but never consulted, allowing corrupted lazy-field bytes to be silently treated as a valid default across subsequent operations - (File: `java/core/src/main/java/com/google/protobuf/LazyFieldLite.java`)

### Summary
The Fei report's failed invariant is that a state-tracking flag meant to gate correctness of consumed data (`invertOraclePrice`/`isOutdated`) is applied inconsistently across otherwise-parallel implementations, so some code paths silently consume data that should have been flagged as invalid/stale. The same class of bug exists in protobuf-java's two parallel "lazy field" implementations for extension/`MessageSet` fields: `LazyFieldLite` and `InternalLazyField`. Both track a `corrupted` boolean when the delayed bytes fail to parse, but only `InternalLazyField` checks it before further use; `LazyFieldLite` sets the flag and then never reads it again.

### Finding Description
`InternalLazyField` documents and enforces an explicit invariant: "If corrupted is true, value must be null" [1](#0-0) , and its `ensureInitialized()` both sets `corrupted = true` on parse failure and fails fast on any *subsequent* access attempt by throwing `InvalidProtocolBufferException("Repeat access to corrupted lazy field")` [2](#0-1) .

`LazyFieldLite` implements the conceptually identical mechanism — a `private volatile boolean corrupted` field — and sets it to `true` in `ensureInitialized(MessageLite defaultInstance)` when `InvalidProtocolBufferException` is caught while parsing `delayedBytes` [3](#0-2) . However, `corrupted` is never read anywhere else in the class: `merge()`, `mergeFrom()`, `getValue()`, `toByteString()`, `equals()`, and `containsDefaultInstance()` all operate purely on `value`/`delayedBytes`/`memoizedBytes` state, with no gate on `corrupted` [4](#0-3) [5](#0-4) .

Once a parse failure occurs, `ensureInitialized` sets `value = defaultInstance` and `memoizedBytes = ByteString.EMPTY` [6](#0-5) . `containsDefaultInstance()` then reports `true` for this field purely because `memoizedBytes.isEmpty()` [7](#0-6) , and `merge()`'s first branch, `if (other.containsDefaultInstance()) { return; }`, silently discards it as if it were a legitimately empty/absent field rather than data that failed integrity validation [8](#0-7) . This is exactly the pattern in the source report: a validity/staleness signal exists in the data model but one of two structurally similar consumers of that signal never consults it, so corrupted state is silently treated as trustworthy default data instead of being surfaced or blocked.

### Impact Explanation
This is a data-integrity issue rather than memory-safety: an attacker sending a malformed/corrupted extension or `MessageSet` payload through a public `parseFrom`/`mergeFrom` API can cause the corrupted bytes to be silently absorbed as an empty/default value in `LazyFieldLite`, with no exception and no distinguishable behavior from a legitimately absent field. Subsequent `merge()` calls combining this field with another `LazyFieldLite` will treat the corrupted source as `containsDefaultInstance() == true` and simply discard it, whereas `InternalLazyField`'s equivalent code path throws `InvalidProtobufRuntimeException` on any further access to a corrupted field. The inconsistency means identical attacker input produces silently-swallowed corruption in one lazy-field implementation and a loud, catchable failure in the other — undermining the ability of calling code to detect that invalid/tampered extension data was received.

### Likelihood Explanation
`LazyFieldLite` backs `MessageLite`-level (lite runtime / `ExtensionRegistryLite`) lazily-parsed message fields and `MessageSet` extensions, which are reachable directly from any public `parseFrom`/`mergeFrom` on a message containing such fields, using ordinary bounded binary Protobuf input from an untrusted client. No privileged access or hostile schema is required — only a message definition (fully trusted) with a lazy-eligible extension field and attacker-supplied bytes for that field.

### Recommendation
Make `LazyFieldLite`'s corruption handling consistent with `InternalLazyField`: check the `corrupted` flag in `ensureInitialized`/`getValue`/`merge`/`mergeFrom`/`containsDefaultInstance` and either (a) fail fast with a well-defined exception on repeat access to a corrupted field, matching `InternalLazyField`, or (b) explicitly document and audit that "silently treat as default" is the intended, safe behavior for every caller of `merge`/`equals`/`toByteString`, and add tests asserting corrupted lazy fields are never conflated with genuinely-absent fields during merges.

### Proof of Concept
1. Construct a message type with a lite-runtime extension field that is eligible for lazy parsing (`LazyFieldLite`).
2. Craft a serialized payload where the extension's length-delimited bytes are syntactically valid at the wire-format level (so `CodedInputStream.readBytes()` succeeds and `delayedBytes` is populated) but are not a valid encoding of the extension's message type (so a later `parseFrom(delayedBytes, extensionRegistry)` throws `InvalidProtocolBufferException`).
3. Call `parseFrom` on the outer message once with a registry causing the extension to be lazily deferred, then trigger `getValue()`/`ensureInitialized` once (setting `corrupted = true`, `value = defaultInstance`, `memoizedBytes = EMPTY`) per [9](#0-8) .
4. Call `merge()` with a second `LazyFieldLite` holding legitimate bytes for the same extension type — observe that `other.containsDefaultInstance()`/`this.containsDefaultInstance()` treats the corrupted field as empty and it is discarded/overwritten without any signal that corrupted data was present, per [8](#0-7) , unlike the exception-throwing behavior of the equivalent `InternalLazyField.mergeFrom` path at [2](#0-1) .

Note: I was not able to execute this PoC in a live JVM in this session (no runtime/tooling access); the trace above is based on static code reading of the cited methods. A Devin session with build/test tooling would be needed to actually run the repro and confirm observable behavior (e.g., via a unit test asserting `merge()` outcomes for a deliberately-corrupted `LazyFieldLite`).

### Citations

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L18-20)
```java
 * <p>The invariants of InternalLazyField are that 1) once value or bytes is set, they will not be
 * changed; 2) value and bytes cannot be null at the same time; 3) If corrupted is true, value must
 * be null.
```

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L202-228)
```java
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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L169-176)
```java
  /**
   * Determines whether this LazyFieldLite instance represents the default instance of this type.
   */
  public boolean containsDefaultInstance() {
    return (memoizedBytes != null && memoizedBytes.isEmpty())
        || (value == null && (delayedBytes == null || delayedBytes.isEmpty()))
        || (defaultInstance != null && value == defaultInstance);
  }
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L280-326)
```java
  public void merge(LazyFieldLite other) {
    if (other.containsDefaultInstance()) {
      return;
    }
    if (this.containsDefaultInstance()) {
      set(other);
      return;
    }

    // If the other field has an extension registry but this does not, copy over the other extension
    // registry.
    if (this.extensionRegistry == null) {
      this.extensionRegistry = other.extensionRegistry;
    }

    // The above checks guarantee that both `other` and `this` have `value` and/or `delayedBytes`
    // set. When both are unset it is considered a containsDefaultInstance==true case.

    // If both sides have delayed bytes we simply concatenate the bytes to save time, but we can
    // only safely do this if the extension registries are the same.
    if (this.delayedBytes != null
        && other.delayedBytes != null
        && this.extensionRegistry == other.extensionRegistry) {
      this.delayedBytes = this.delayedBytes.concat(other.delayedBytes);
      return;
    }

    // If either side is parsed, we merge on parsed instances.
    if (this.value != null || other.value != null) {
      mergeValue(other);
      return;
    }

    // If we have reached this far, both sides have `delayedBytes` set and neither have `value` set.

    // If `this.defaultInstance` is not known, we can't trigger a parse of `this` and so the
    // best we can do is concat the bytes. Since other side's extension registry is different
    // this may result in some extensions being lost that shouldn't have been, but its the
    // best that we can do if we reach this point and is not expected to occur in real use.
    if (defaultInstance == null) {
      // TODO: b/467739361 - Consider throwing an exception here.
      this.delayedBytes = this.delayedBytes.concat(other.delayedBytes);
      return;
    }

    setValue(mergeValueAndBytes(getValue(), other.delayedBytes, other.extensionRegistry));
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
