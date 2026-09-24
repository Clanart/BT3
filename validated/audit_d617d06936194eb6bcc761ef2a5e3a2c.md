### Title
Silent swallow of `InvalidProtocolBufferException` in `LazyFieldLite` causes parsed-value/wire-bytes divergence, bypassing downstream validation of lazy extension fields - (File: `java/core/src/main/java/com/google/protobuf/LazyFieldLite.java`)

### Summary
`LazyFieldLite` (used to store `[lazy = true]` extension/message fields parsed from untrusted binary input) treats a failed sub-message parse identically to "field genuinely empty/default", exactly the invariant collapse exploited in the Zora report (a caught failure is silently converted into the same code path as a normal, safe outcome). Unlike the hardened sibling class `InternalLazyField`, which records a `corrupted` flag and either rethrows (`lazyExtensionEnabled()`) or documents the fallback, `LazyFieldLite.ensureInitialized()` and `mergeValueAndBytes()` swallow `InvalidProtocolBufferException` with no signal to the caller, and — critically — do not clear `delayedBytes`, so the accessor used for parsed-value decisions (`getValue()`) and the accessor used for re-serialization (`toByteString()`/`getSerializedSize()`) diverge after a corruption event.

### Finding Description
`ensureInitialized` (java/core/src/main/java/com/google/protobuf/LazyFieldLite.java:468-496): [1](#0-0) 
catches `InvalidProtocolBufferException` from parsing attacker-supplied `delayedBytes`, sets `corrupted = true`, and forces `value = defaultInstance`, `memoizedBytes = ByteString.EMPTY` — but it never clears `delayedBytes`.

`toByteString()` (java/core/src/main/java/com/google/protobuf/LazyFieldLite.java:406-427) checks `delayedBytes` first: [2](#0-1) 
Because `delayedBytes` was never nulled out on corruption, `toByteString()` and `getSerializedSize()` (line 392-404) continue to return the original, unmodified attacker bytes even though `getValue()` returns a harmless default instance.

`mergeValueAndBytes` (java/core/src/main/java/com/google/protobuf/LazyFieldLite.java:368-377) has the identical pattern for the merge path — an incoming malformed byte string is silently discarded with the comment "Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto was invalid," and `mergeFrom` (lines 334-366) reaches this same swallow-and-continue behavior when concatenation of raw bytes across two lazy fields with different extension registries isn't possible.

This mirrors the report's root cause precisely: a `try { ... } catch (...) { return <benign-looking result>; }` pattern collapses "genuinely empty/no-op" and "attacker deliberately corrupted this input" into one indistinguishable, silently-successful code path, so any caller relying on the graceful (non-exceptional) return believes the safe/default state was reached honestly. Here, the "next risky stage" analog is: application code that reads `message.getExtension(...)` (via `getValue()`) to make a trust/policy decision sees an innocuous default instance, while any code that forwards, signs, hashes, or re-serializes the very same object (via `toByteString()`) transmits the original tampered bytes unchanged. A second consumer of those forwarded bytes (a different microservice, a different protobuf runtime, or a stricter parser) can interpret the corrupted sub-message differently than "empty," producing a parser/consumer trust bypass — the object that was validated is not the object that gets propagated.

### Impact Explanation
Applications using `[lazy = true]` extensions (proto2 lite runtime) commonly perform authorization/validation logic against the parsed extension value and then relay/store/re-transmit the same message. Because `LazyFieldLite` presents two inconsistent views of the same field after a corruption event (default value via `getValue()`, but original attacker bytes via `toByteString()`), an attacker can craft a message whose extension appears "empty/default" to validating code, while the outgoing wire bytes still carry the malicious/malformed payload to whatever system consumes the retransmitted bytes. This is a genuine integrity/trust-boundary bypass consistent with High-severity classification for this bug class (silent-failure-enables-bypass), though it depends on an application's specific use of `getValue()` for a security decision followed by re-serialization of the same instance — an exposure assumption that must hold for the impact to manifest (stated per instructions since Protobuf itself has no RPC endpoint).

### Likelihood Explanation
Reachable via the standard public binary parse path: any client sending bounded binary Protobuf that includes a malformed byte sequence for a schema-declared lazy extension field will trigger this code deterministically. No privileged access, hostile schema, or resource exhaustion is required — only a trusted schema with a `[lazy = true]` extension and an ordinary malformed sub-message payload, matching the stated attacker model.

### Recommendation
- Clear `delayedBytes` (and `memoizedBytes`) consistently with `value` whenever a corruption is detected in `ensureInitialized()`, so `toByteString()`/`getSerializedSize()` reflect the same (default) state as `getValue()`, eliminating the parsed-value/wire-bytes divergence.
- Surface the `corrupted` state to callers (as `InternalLazyField` already does via `getValue()` throwing `InvalidProtobufRuntimeException` when `lazyExtensionEnabled()`), rather than silently discarding the exception in `LazyFieldLite.ensureInitialized()` and `mergeValueAndBytes()`.
- Align `LazyFieldLite`'s error handling with `InternalLazyField`'s documented invariant ("once value or bytes is set, they will not be changed; value and bytes cannot be null at the same time"), and deprecate/migrate remaining `LazyFieldLite` call sites to the hardened `InternalLazyField` implementation.

### Proof of Concept
1. Define a proto2 message with an extension field marked `[lazy = true]` whose type is any message (schema trusted).
2. Craft a binary payload for the outer message that includes a valid tag/length for the lazy extension field, but a body that is not a valid encoding of the extension's message type (e.g., a length-delimited field with garbage/truncated content that fails wire-type validation).
3. Parse with the generated lite-message `parseFrom(bytes)`; parsing succeeds without exception because `LazyFieldLite` defers parsing.
4. Call `message.getExtension(ext)` — this triggers `ensureInitialized()` at java/core/src/main/java/com/google/protobuf/LazyFieldLite.java:477-495, which catches the parse failure, sets `corrupted = true` internally (never exposed), and returns `defaultInstance`.
5. Call `message.toByteString()` on the same object — because `delayedBytes` was never cleared, `toByteString()` (lines 406-415) returns the original malformed bytes verbatim, not a byte-serialization of the observed default-instance value.
6. Assert: `message.getExtension(ext).equals(defaultInstance)` is `true`, yet `message.toByteString()` differs from `defaultInstance-serialized-message.toByteString()` and instead contains the original malformed payload — demonstrating the divergence between the "safe" value seen by application logic and the actual bytes that would be forwarded/stored.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L406-415)
```java
  /** Returns a BytesString for this field in a thread-safe way. */
  public ByteString toByteString() {
    // We *must* return delayed bytes if it was set because the dependent messages may have
    // memoized serialized size based off of it.
    if (delayedBytes != null) {
      return delayedBytes;
    }
    if (memoizedBytes != null) {
      return memoizedBytes;
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
