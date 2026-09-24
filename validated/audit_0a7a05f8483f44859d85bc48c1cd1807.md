### Title
`TypeRegistry` resolves Any messages by fully-qualified type name only, causing silent type confusion when duplicate names are registered - (File: `java/util/src/main/java/com/google/protobuf/util/JsonFormat.java`, `java/core/src/main/java/com/google/protobuf/TypeRegistry.java`)

### Summary
The Y2K Finance report shows a mapping (`tokenToOracle`) keyed on a single component of a logical pair, that silently drops a legitimate new entry (`if already set, skip`) instead of erroring, causing the wrong oracle to be silently reused for a semantically different pair. Protobuf's `TypeRegistry`, used to resolve `google.protobuf.Any` messages during JSON parsing/printing, has the same structural flaw: it is keyed only by the message's fully-qualified type name (a `String`), not by the full identity of the schema (which `FileDescriptor`/pool it originates from). When two different message definitions sharing the same fully-qualified name are added to the same registry, the builder silently keeps the first one and drops the second, exactly the "skip and keep old value" pattern in the report.

### Finding Description
`TypeRegistry.Builder.addMessage()` keys the registry purely on `message.getFullName()`: [1](#0-0) 
If a name collision occurs (two distinct `Descriptor`s that happen to share the same fully-qualified name, e.g. loaded from two independently-built `FileDescriptorSet`s/pools in the same trusted application, a scenario protobuf explicitly warns about as unsupported/unchecked), the second definition is silently discarded with only a `logger.warning`, and lookups continue to resolve to the first-registered `Descriptor`: [2](#0-1) 

This registry is the sole mechanism `JsonFormat` uses to resolve the schema for `Any.type_url` when parsing/printing `Any` fields (see class doc: "You must provide a TypeRegistry containing all message types used in Any message fields"): [3](#0-2) 
`find(name)`/`getDescriptorForTypeUrl(typeUrl)` do a flat lookup by name with no notion of "which pool/pair this name belongs to": [4](#0-3) 

The failed invariant transferring from the report: the key space (type full name) is not fine-grained enough to disambiguate two distinct schemas that an application legitimately wants to keep separate, and the collision-handling policy silently keeps stale/wrong data instead of failing loudly — mirroring `tokenToOracle[_token]` being keyed on one token instead of the pair, and silently preserving the old oracle on conflict.

### Impact Explanation
Consuming applications that build a `TypeRegistry`/`JsonFormat.TypeRegistry` from multiple sources (e.g., aggregating descriptors from separate `FileDescriptorSet`s, plugin-supplied schemas, or two versions of a same-named message during a rollout) can end up with a bound `Any.type_url` name resolving, for JSON parsing of a client-supplied Any payload, to the wrong `Descriptor` — one from an unrelated/incompatible schema that happens to share the fully-qualified name. Because the bytes are then interpreted against a mismatched `Descriptor`, this is a type-confusion condition affecting the integrity of the decoded application object (fields silently misinterpreted, wrong field types/semantics applied), analogous to the oracle mismatch producing incorrect price data. It does not, by itself, grant memory corruption or code execution; it is a logic-correctness/integrity issue triggered by how the registry accumulates entries, not an attacker-forced parser vulnerability.

### Likelihood Explanation
This requires the trusted host application to register two distinct message definitions under the same fully-qualified type name in one `TypeRegistry` (documented in the API's own Javadoc as a known duplicate-name caveat, "you may want to create a layer on top to control your intended behavior in the face of duplicates"), so it is a "consuming-application exposure" scenario, not a directly attacker-forced condition in bounds of a single, well-defined proto schema. The trigger surface is real (multi-schema aggregation, versioned rollouts, dynamically-loaded `FileDescriptorSet`s) but is lower likelihood than a pure wire-format parsing bug because it depends on how the application composes its `TypeRegistry`, matching the disputed/Medium severity treatment given to the original oracle report.

### Recommendation
Key the `TypeRegistry` map on the identity of the message descriptor's origin (e.g., `(FileDescriptor identity, full name)` or a pair combining the owning pool with the name) rather than solely on `String fullName`, and make duplicate-name registration with differing `Descriptor`s a hard error (throw) instead of a silent `logger.warning` + skip, so applications cannot silently end up resolving `Any` payloads against the wrong schema.

### Proof of Concept
1. Build two independent `FileDescriptorProto`s (e.g. compiled separately, simulating two plugin-supplied schemas) that each declare a message named `foo.Data` but with different field layouts (e.g. `int32 value = 1;` vs `string value = 1;`).
2. Construct two `Descriptor`s from these files and add both to one `TypeRegistry.Builder`:
```java
TypeRegistry registry = TypeRegistry.newBuilder()
    .add(fooDataV1.getDescriptor())
    .add(fooDataV2.getDescriptor())   // same full name "foo.Data", different schema
    .build();
```
Per `TypeRegistry.Builder.addMessage()` at [5](#0-4) , the second `add` is silently dropped and only logs a warning.
3. Use `JsonFormat.parser().usingTypeRegistry(registry)` to parse an `Any` JSON payload whose `@type` is `.../foo.Data` but whose bytes were actually encoded against `fooDataV2`'s schema. The parser resolves the type via `registry.find("foo.Data")`, which returns `fooDataV1`'s `Descriptor`, and the payload is decoded under the wrong schema — demonstrating the same "silent wrong-oracle" substitution described in the original report, transplanted to protobuf's type resolution for `Any`.

(Note: I was unable to fully trace the exact `parseAny`/`mergeAny` method body in `JsonFormat.java` within this session — grep for `parseAny`/`registry.find` inside that file returned no textual matches, likely due to different internal method naming or index truncation. The class-level contract and `getDescriptorForTypeUrl` call sites confirm the registry is the resolution mechanism for `Any`, but the precise line numbers of the parse-time lookup could not be pinned down here. A full Devin session with direct file access would be needed to extract exact line numbers for the parse-side call and produce a compiled, executed reproduction.)

### Citations

**File:** java/core/src/main/java/com/google/protobuf/TypeRegistry.java (L131-142)
```java
    private void addMessage(Descriptor message) {
      for (Descriptor nestedType : message.getNestedTypes()) {
        addMessage(nestedType);
      }

      if (types.containsKey(message.getFullName())) {
        logger.warning("Type " + message.getFullName() + " is added multiple times.");
        return;
      }

      types.put(message.getFullName(), message);
    }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L643-657)
```java
  /**
   * A TypeRegistry is used to resolve Any messages in the JSON conversion. You must provide a
   * TypeRegistry containing all message types used in Any message fields, or the JSON conversion
   * will fail because data in Any message fields is unrecognizable. You don't need to supply a
   * TypeRegistry if you don't use Any message fields.
   */
  public static class TypeRegistry {
    private static class EmptyTypeRegistryHolder {
      private static final TypeRegistry EMPTY =
          new TypeRegistry(Collections.<String, Descriptor>emptyMap());
    }

    public static TypeRegistry getEmptyTypeRegistry() {
      return EmptyTypeRegistryHolder.EMPTY;
    }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L663-675)
```java
    /**
     * Find a type by its full name. Returns null if it cannot be found in this {@link
     * TypeRegistry}.
     */
    @Nullable
    public Descriptor find(String name) {
      return types.get(name);
    }

    @Nullable
    Descriptor getDescriptorForTypeUrl(String typeUrl) throws InvalidProtocolBufferException {
      return find(getTypeName(typeUrl));
    }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L734-745)
```java
      private void addMessage(Descriptor message) {
        for (Descriptor nestedType : message.getNestedTypes()) {
          addMessage(nestedType);
        }

        if (types.containsKey(message.getFullName())) {
          logger.warning("Type " + message.getFullName() + " is added multiple times.");
          return;
        }

        types.put(message.getFullName(), message);
      }
```
