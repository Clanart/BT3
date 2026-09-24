## Title
`InternalLazyField` required-field state is never consolidated before `isInitialized()`/`build()` checks - (File: `java/core/src/main/java/com/google/protobuf/FieldSet.java`)

## Summary
The PoolTogether bug stemmed from `send`/`transfer`/`transferFrom` acting on stale account state because they skipped `consolidateBalanceOf()`, the one function that "syncs" a user's true balance before it is used. The Protobuf analog is `FieldSet.isMessageFieldValueInitialized()`, which is the "sync" checkpoint for the `isInitialized()` invariant: it must reflect the *true, fully-parsed* state of a message field before required-field validation succeeds/fails. For ordinary (eagerly-parsed) message fields it does exactly that by delegating to `MessageLiteOrBuilder.isInitialized()`. But for `InternalLazyField`-backed values (used for lazily-parsed extension/`MessageSet` fields), it unconditionally returns `true` without ever parsing (`ensureInitialized`/`getValue()`) the underlying bytes. [1](#0-0) 

## Finding Description
`FieldSet.isInitialized()` walks all fields and, for message-typed fields, calls the private helper `isMessageFieldValueInitialized`: [2](#0-1) 

```java
private static boolean isMessageFieldValueInitialized(Object value) {
  if (value instanceof MessageLiteOrBuilder) {
    return ((MessageLiteOrBuilder) value).isInitialized();
  } else if (value instanceof InternalLazyField) {
    return true;
  } else {
    throw new IllegalArgumentException(...);
  }
}
```

`InternalLazyField` is the storage object used when a MessageSet/lazy extension is populated from wire bytes without immediately parsing them — mirroring the "scheduled but not yet consolidated" Pod-token state in the ERC777 report. The invariant that should hold is: *before code asks "is this field/message initialized" (i.e., are all required sub-fields present), the lazy bytes must be parsed (`ensureInitialized`/`getValue()`) so the check reflects the actual content*, exactly as the Pod contract needed `consolidateBalanceOf()` before `send`/`transfer` could correctly reflect a user's true balance. Instead, the helper simply assumes `true` for any `InternalLazyField`, regardless of whether the underlying bytes actually contain all required fields for that message type.

This is invoked from `FieldSet.Builder.isInitialized()` used by generated `Message.Builder.isInitialized()`/`build()` code paths (`buildImpl(false)` triggers required-field validation and throws `UninitializedMessageException` on failure): [3](#0-2) [4](#0-3) 

`InternalLazyField` itself demonstrates that the true state is only known after `ensureInitialized()` triggers a real parse: [5](#0-4) 

So the "consolidation" primitive (`getValue()`/`ensureInitialized()`) exists and is used elsewhere (e.g., `mergeFromField`, `getField`), but is deliberately bypassed in the required-field check.

## Impact Explanation
Because `isMessageFieldValueInitialized` never parses the lazy bytes, a message containing a lazily-parsed extension/MessageSet field with missing required sub-fields will be reported as "initialized" by `FieldSet.isInitialized()`/`Message.Builder.isInitialized()`. Downstream consumers that rely on `isInitialized()` (or the `UninitializedMessageException` thrown from `build()`) to enforce schema required-field guarantees before trusting/forwarding a message will incorrectly treat an incomplete, wire-produced message as fully valid. This is an integrity failure of the required-field invariant on ordinary, bounded, attacker-supplied binary Protobuf input parsed through the public parse API (lazy extension parsing is triggered by ordinary `parseFrom`/`mergeFrom` when the registry marks the extension type lazy), consistent with the "ordinary client sending bounded binary Protobuf" threat model. It does not itself cause memory corruption/RCE, but silently violates the schema-required-field contract that calling applications depend on for correctness/security decisions — a Medium-severity integrity bug analogous in class (skipped state-consolidation before a semantically important check) to the referenced ERC777 finding.

## Likelihood Explanation
Reachable whenever: (1) a proto schema has `required` fields inside a message type used as a MessageSet extension or otherwise routed through `InternalLazyField` (lazy extension parsing must be enabled, `ExtensionRegistryLite.lazyExtensionEnabled()`), and (2) the attacker sends bytes for that extension omitting a required field, then (3) any code calls `isInitialized()`/`build()` on the containing message before ever calling a getter that triggers `ensureInitialized()`. No privileged access, forged tables, or huge payloads are required — this is a straightforward bounded binary input through the standard parse path.

## Recommendation
Make `isMessageFieldValueInitialized` consolidate lazy fields before checking, mirroring the fix pattern used in the ERC777 report (force a "consolidate" call before the state-dependent operation):
```java
} else if (value instanceof InternalLazyField) {
  return ((InternalLazyField) value).getValue().isInitialized();
}
```
This trades the laziness optimization for correctness of the required-field invariant, or alternatively documents/enforces that lazy extension types must never contain required fields (validated at compile/descriptor-build time).

## Proof of Concept
Conceptual reproduction (would need to be executed by an agent with repo access to confirm):
1. Define a proto with a required-field message type registered as a lazy MessageSet extension (`lazyExtensionEnabled()` = true, extension not eagerly parsed).
2. Serialize wire bytes for that extension omitting the required field.
3. `SomeMessage.parseFrom(bytes, registry)` — this stores the extension as an `InternalLazyField` without parsing it (per `MessageReflection.mergeMessage`, `java/core/src/main/java/com/google/protobuf/MessageReflection.java:1073-1103`).
4. Call `message.toBuilder().isInitialized()` (or `.build()`).
5. Expected: should return `false` / throw `UninitializedMessageException` due to the missing required field.
6. Actual (per code path traced above): `isMessageFieldValueInitialized` returns `true` immediately for the `InternalLazyField`, so `isInitialized()` returns `true` and `build()` succeeds despite the missing required field.

I was not able to execute this PoC in this environment (no code execution tool available in ask-only mode); the trace above is based on static reading of `FieldSet.java`, `InternalLazyField.java`, and `MessageReflection.java`. A Devin session with build/test tooling would be needed to actually run and confirm the reproduction.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/FieldSet.java (L464-497)
```java
  // Avoid iterator allocation.
  @SuppressWarnings({"ForeachList", "ForeachListWithUserVar"})
  private static <T extends FieldDescriptorLite<T>> boolean isInitialized(
      final Map.Entry<T, Object> entry) {
    final T descriptor = entry.getKey();
    if (descriptor.getLiteJavaType() == WireFormat.JavaType.MESSAGE) {
      if (descriptor.isRepeated()) {
        List<?> list = (List<?>) entry.getValue();
        int listSize = list.size();
        for (int i = 0; i < listSize; i++) {
          Object element = list.get(i);
          if (!isMessageFieldValueInitialized(element)) {
            return false;
          }
        }
      } else {
        return isMessageFieldValueInitialized(entry.getValue());
      }
    }
    return true;
  }

  private static boolean isMessageFieldValueInitialized(Object value) {
    if (value instanceof MessageLiteOrBuilder) {
      // Message fields cannot have builder values in FieldSet, but can in FieldSet.Builder, and
      // this method is used by FieldSet.Builder.isInitialized.
      return ((MessageLiteOrBuilder) value).isInitialized();
    } else if (value instanceof InternalLazyField) {
      return true;
    } else {
      throw new IllegalArgumentException(
          "Wrong object type used with protocol message reflection.");
    }
  }
```

**File:** java/core/src/main/java/com/google/protobuf/FieldSet.java (L981-1011)
```java
    public FieldSet<T> build() {
      return buildImpl(false);
    }

    /** Creates the FieldSet but does not validate that all required fields are present. */
    public FieldSet<T> buildPartial() {
      return buildImpl(true);
    }

    /**
     * Creates the FieldSet.
     *
     * @param partial controls whether to do a build() or buildPartial() when converting submessage
     *     builders to messages.
     */
    private FieldSet<T> buildImpl(boolean partial) {
      if (fields.isEmpty()) {
        return FieldSet.emptySet();
      }
      isMutable = false;
      SmallSortedMap<T> fieldsForBuild = fields;
      if (hasNestedBuilders) {
        // Make a copy of the fields map with all Builders replaced by Message.
        fieldsForBuild =
            cloneAllFieldsMap(fields, /* copyList= */ false, /* resolveLazyFields= */ false);
        replaceBuilders(fieldsForBuild, partial);
      }
      FieldSet<T> fieldSet = new FieldSet<>(fieldsForBuild);
      fieldSet.hasLazyField = hasLazyField;
      return fieldSet;
    }
```

**File:** java/core/src/main/java/com/google/protobuf/FieldSet.java (L1321-1329)
```java
    public boolean isInitialized() {
      int n = fields.size(); // Optimisation: hoist out of hot loop.
      for (int i = 0; i < n; i++) {
        if (!FieldSet.isInitialized(fields.getArrayEntryAt(i))) {
          return false;
        }
      }
      return true;
    }
```

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L202-229)
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
  }
```
