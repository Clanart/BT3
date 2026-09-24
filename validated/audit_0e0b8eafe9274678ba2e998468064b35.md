## Analog Identified: Silent Swallowing of `InvalidProtocolBufferException` in `LazyFieldLite`

### Title
Silent Data Corruption via Swallowed `InvalidProtocolBufferException` in Lazy Extension Field Parsing/Merging - (File: `java/core/src/main/java/com/google/protobuf/LazyFieldLite.java`)

### Summary
The external report's failed invariant is: *an inner operation can fail, but the outer operation reports success while silently discarding the failed effect, leaving persistent state inconsistent with what the caller believes happened.* In `EnglishAuctionCollateralLiquidator.sol`, a `try/catch {}` around a callback swallows the callback's revert, so `liquidate()` "succeeds" while a required state update (liquidity restoration) never happens. The Protobuf analog is `LazyFieldLite`, which lazily parses/merges nested message and MessageSet-extension bytes and swallows `InvalidProtocolBufferException` in three places, silently discarding the corrupted bytes and substituting a default/stale value instead of propagating the failure to the top-level `parseFrom()` caller.

### Finding Description
`LazyFieldLite` stores extension/message-set field bytes (`delayedBytes`) and lazily parses them on first access via `ensureInitialized()`: [1](#0-0) 

When the lazily-parsed bytes are actually invalid wire data, the `catch (InvalidProtocolBufferException e)` block does not rethrow, log, or otherwise surface the error to the public `parseFrom()`/`getValue()` API — it just marks a package-private `corrupted` flag (not exposed through any public getter) and substitutes `defaultInstance` as if parsing had succeeded: [2](#0-1) 

The same silent-swallow pattern occurs during merge of two `LazyFieldLite` instances carrying the same MessageSet extension type (e.g., two `Item`s with the same `typeId` in a `MessageSet`, where the second occurrence's raw bytes are merged into the already-parsed first value): [3](#0-2) 

Because `delayedBytes`/`memoizedBytes` are still round-tripped verbatim by `writeTo()`/`toByteString()` whenever they remain non-null, and because `containsDefaultInstance()`/`equals()` reason about `value` rather than "did parsing actually succeed," a caller invoking the standard public `parseFrom()` on a message containing a corrupted lazy/MessageSet extension observes:
- No exception thrown — `parseFrom()` returns normally.
- The extension silently reverts to its default/empty value (data loss), or — in the merge path — silently keeps only the first value and drops the second occurrence's payload entirely, with no signal to the caller.

This exactly mirrors the reported invariant break: the "outer" operation (`parseFrom`) reports success while the "inner" effect (a specific field's data) failed and was discarded, and there is no public way for an ordinary caller of the parsing API to detect this at the point of failure — only `isCorrupted()`, which is package-private and never surfaced by `parseFrom()`, `getValue()`, `equals()`, or `hashCode()`.

### Impact Explanation
An attacker who controls the extension bytes of a `MessageSet`-formatted message field (a legitimate, bounded binary protobuf payload delivered through the standard public parse API) can craft an extension payload that is malformed. Instead of the parse failing loudly (as normal Protobuf semantics require — malformed input should cause `InvalidProtocolBufferException`), the message parses "successfully" with that extension silently reset to its default value or with a legitimate update silently dropped during merge. Any consuming application that trusts `parseFrom()` success as proof that the entire message — including its extensions — was faithfully deserialized will operate on incomplete/incorrect data without any exception or corruption signal, a data-integrity violation analogous to the reported liquidity-restoration-never-happened outcome.

### Likelihood Explanation
This requires only a MessageSet-formatted message with an extension field that a public parse API deserializes through `LazyFieldLite`/`MessageSetSchema`; no privileged access or special environment is needed — an ordinary bounded binary protobuf payload from any consuming application's parse boundary triggers this path. Likelihood is bounded by the increasingly narrow use of proto2 MessageSet wire format in modern schemas, but where used it is deterministically triggerable with a crafted extension byte string.

### Recommendation
`ensureInitialized()`, `mergeFrom(CodedInputStream, ExtensionRegistryLite)`, and `mergeValueAndBytes()` should not silently swallow `InvalidProtocolBufferException`. At minimum, the corrupted state should be surfaced through the public API (e.g., have `parseFrom()`/`getValue()` propagate the exception, or have `isCorrupted()` be checked and honored by callers such as `equals()`/serialization) so a consuming application can detect and reject a message containing corrupted lazy/MessageSet extension data rather than silently operating on a partially-parsed message.

### Proof of Concept
1. Construct a proto2 message using `message_set_wire_format` with an extension registered for type `X`.
2. Encode a `MessageSet` `Item` for type `X` whose `message` field bytes are valid length-delimited data but are not a valid serialization of `X` (e.g., a wire type mismatch inside `X`'s schema, or a truncated nested field only detectable during real deserialization).
3. Call the extension's getter (which triggers `LazyFieldLite.ensureInitialized()`) after `parseFrom()` returns without exception — the extension will silently equal `X`'s default instance rather than throwing, even though the wire bytes were invalid.
4. Alternatively, encode two `Item`s with the same `typeId`; if the first is eagerly parsed and the second's raw bytes fail to merge, `mergeFrom()` at lines 360–365 discards the second occurrence silently, with `parseFrom()` reporting overall success. [4](#0-3)

### Citations

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L360-376)
```java
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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L507-510)
```java
  /** Returns whether the lazy field was corrupted and replaced with an empty message. */
  boolean isCorrupted() {
    return corrupted;
  }
```
