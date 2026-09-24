### Title
Silent swallowing of `InvalidProtocolBufferException` during lazy-field merge causes untrusted-input corruption to be masked as success - ([File: java/core/src/main/java/com/google/protobuf/LazyFieldLite.java])

### Summary
`LazyFieldLite.mergeFrom()` and its helper `mergeValueAndBytes()` catch `InvalidProtocolBufferException` thrown while merging attacker-controlled bytes into an already-parsed lazy message field, and discard the error entirely: no exception is re-thrown, nothing is logged, and the operation silently falls back to the pre-merge value. This mirrors the reported ERC20 pattern of ignoring a call's failure signal (`transfer()`'s boolean) and letting the caller believe the operation succeeded, causing silent state divergence from what was actually persisted/observed.

### Finding Description
`LazyFieldLite` stores lazily-parsed submessage/extension bytes coming directly from `CodedInputStream.mergeFrom`/parsing of untrusted wire-format input [1](#0-0) .

When merging a new chunk of bytes into an already-parsed value, two code paths swallow the parse-failure signal instead of propagating it:

```java
try {
  setValue(value.toBuilder().mergeFrom(input, extensionRegistry).build());
} catch (InvalidProtocolBufferException e) {
  // Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto
  // was invalid.
}
``` [2](#0-1) 

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
``` [3](#0-2) 

This is exercised from `merge()`, which is invoked whenever two messages containing the same lazily-decoded field (typical of proto2 `MessageSet` extensions and `lazy=true` submessage fields) are combined during parsing of repeated/duplicate fields in the wire stream [4](#0-3) . `mergeFrom(CodedInputStream, ExtensionRegistryLite)` is called directly while decoding a message that repeats the same field number for a lazy field [5](#0-4) .

By contrast, the *initial* lazy parse path (`ensureInitialized`) at least records the failure via an internal `corrupted` flag before substituting the default instance [6](#0-5) , but `isCorrupted()` is a package-private accessor with no caller anywhere in the runtime (`grep` for `isCorrupted` finds only its own declaration) — so even that signal is not surfaced to application code. The merge-path failures (`mergeFrom`, `mergeValueAndBytes`) don't even set that flag, so there is no internal record at all that a merge silently discarded malformed attacker bytes.

The failed invariant that transfers from the ERC20 report: a callable that can fail (returns/throws a failure indicator) has that indicator discarded, and the caller proceeds as though the call succeeded, leaving persistent/observable state inconsistent with what actually happened.

### Impact Explanation
An attacker who controls the second (or later) occurrence of a repeated lazy-field/extension in a length-delimited protobuf message can craft a payload where:
- the first occurrence establishes a legitimate parsed value, and
- the second occurrence is intentionally malformed.

`mergeFrom`/`mergeValueAndBytes` will catch the resulting `InvalidProtocolBufferException` and keep the *first* occurrence's value, with zero indication to the calling application that part of the wire input was rejected. Any application logic that assumes "parse succeeded fully or threw" (e.g., audit logging, validation gates, equality/consistency checks against the raw bytes) can be silently bypassed: the deserialized object's semantic value diverges from a truthful representation of the wire bytes, without any exception or log entry — a data-integrity failure directly reachable from ordinary public parsing of bounded, attacker-supplied binary protobuf input.

This is analogous in structure (not severity) to the ERC20 finding: a failure return/exception is discarded, and the caller's downstream logic treats the operation as fully successful when it was not, producing a state/behavior mismatch that the caller cannot detect through the documented API.

### Likelihood Explanation
Reaching this code requires a message schema with a lazy-parsed submessage or `MessageSet`-style extension field and a wire payload that repeats that field number with a corrupted second chunk — both are ordinary, schema-conformant possibilities for any consuming application using lazy fields or MessageSet extensions, requiring no privileged access, hostile schema, or unbounded input. It is a routine occurrence achievable with a single crafted length-delimited protobuf message sent to any public parse entry point that eventually invokes `LazyFieldLite.mergeFrom`/`merge`.

### Recommendation
Do not silently discard `InvalidProtocolBufferException` in `LazyFieldLite.mergeFrom()` and `mergeValueAndBytes()`. At minimum, set the existing `corrupted` flag on failure (consistent with `ensureInitialized`) and expose it through a public/observable API (or re-throw so callers of `Message.Builder.mergeFrom` see the failure, consistent with normal message-parsing semantics elsewhere in the library). This preserves the existing lazy-parsing performance model while ensuring failures are not silently converted into apparent success.

### Proof of Concept
Conceptual reproduction (would need to be executed in the actual Java runtime to confirm the exact byte-level trigger):
1. Define a proto2 message `M` with a `lazy = true` submessage field `Sub sub = 1;` or use a `MessageSet` extension.
2. Build wire bytes for `M` that encode field 1 twice: first with valid bytes for `Sub` (e.g., `sub.foo = 1`), second with a truncated/invalid length-delimited chunk for `Sub`.
3. Parse `M` with `M.parseFrom(bytes)`. Internally, the second occurrence causes `FieldSet`/`GeneratedMessage` to call `LazyFieldLite.mergeFrom(CodedInputStream, extensionRegistry)`, which attempts `value.toBuilder().mergeFrom(input, extensionRegistry).build()`, throws `InvalidProtocolBufferException`, and the catch block at lines 362–365 discards it.
4. Observe that `M.parseFrom(bytes)` returns successfully (no exception propagated to the caller) and `M.getSub()` reflects only the first occurrence's value, with no indication that the second chunk was rejected as malformed — confirming the failure signal is lost to the caller. [7](#0-6)

### Citations

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L12-30)
```java
/**
 * LazyFieldLite encapsulates the logic of lazily parsing message fields. It stores the message in a
 * ByteString initially and then parses it on-demand.
 *
 * <p>LazyFieldLite is thread-compatible: concurrent reads are safe once the proto that this
 * LazyFieldLite is a part of is no longer being mutated by its Builder. However, explicit
 * synchronization is needed under read/write situations.
 *
 * <p>When a LazyFieldLite is used in the context of a MessageLite object, its behavior is
 * considered to be immutable and none of the setter methods in its API are expected to be invoked.
 * All of the getters are expected to be thread-safe. When used in the context of a
 * MessageLite.Builder, setters can be invoked, but there is no guarantee of thread safety.
 *
 * <p>TODO: Consider splitting this class's functionality and put the mutable methods
 * into a separate builder class to allow us to give stronger compile-time guarantees.
 *
 * <p>This class is internal implementation detail of the protobuf library, so you don't need to use
 * it directly.
 *
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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L334-377)
```java
  public void mergeFrom(CodedInputStream input, ExtensionRegistryLite extensionRegistry)
      throws IOException {
    if (this.containsDefaultInstance()) {
      setByteString(input.readBytes(), extensionRegistry);
      return;
    }

    // If the other field has an extension registry but this does not, copy over the other extension
    // registry.
    if (this.extensionRegistry == null) {
      this.extensionRegistry = extensionRegistry;
    }

    // In the case that both of them are not parsed we simply concatenate the bytes to save time. In
    // the (probably rare) case that they have different extension registries there is a chance that
    // some of the extensions may be dropped, but the tradeoff of making this operation fast seems
    // to outway the benefits of combining the extension registries, which is not normally done for
    // lite protos anyways.
    if (this.delayedBytes != null) {
      setByteString(this.delayedBytes.concat(input.readBytes()), this.extensionRegistry);
      return;
    }

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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L469-496)
```java
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
