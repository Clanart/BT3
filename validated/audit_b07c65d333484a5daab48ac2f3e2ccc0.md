### Title
`DynamicMessage.Builder.clear()` fails to reset `oneofCases` array, leaving stale oneof-case metadata after reuse — (File: `java/core/src/main/java/com/google/protobuf/DynamicMessage.java`)

### Summary
In the Unlock Protocol report, `transferFrom()` resets the `keyManager` role in one code branch but omits the reset in a sibling branch, so state that should be tied to the *new* owner instead survives from the *previous* owner, letting the old owner retain elevated control after an ownership transfer. The Protobuf analog is `DynamicMessage.Builder.clear()`, which resets the field-value storage (`fields`, `unknownFields`) but never resets the parallel `oneofCases` array that records "which field of each oneof is currently set." After a `clear()` + reuse/merge cycle — a supported, ordinary usage pattern for the public `Message.Builder` API (builder pooling/reuse across independent, attacker-controlled parses) — the builder's oneof-case bookkeeping can report a case as "set" that no longer corresponds to any actual field value in `fields`.

### Finding Description
`DynamicMessage.Builder` maintains two pieces of state per instance: `fields` (the actual `FieldSet.Builder` holding parsed field values) and `oneofCases` (a `FieldDescriptor[]`, one entry per `oneof`, recording which member field is considered "set" for reflection/API purposes: `hasOneof`, `getOneofFieldDescriptor`, and the special-case merge logic). [1](#0-0) 

`clear()` only resets `fields` and `unknownFields`: [2](#0-1) 

It never resets `oneofCases`. Compare with `setField`/`clearField`, which correctly keep `oneofCases` synchronized with `fields` on every mutation: [3](#0-2) [4](#0-3) 

Because `mergeFrom(Message other)` explicitly consults `oneofCases[i]` to decide whether to clear a previously-set oneof member before merging in a new one: [5](#0-4) 

...a builder that was `clear()`-ed and then reused for a fresh `mergeFrom()`/parse starts with `fields` empty but `oneofCases` still pointing at the field descriptor from the *previous* message's oneof selection. `hasOneof()` / `getOneofFieldDescriptor()` on the "cleared" builder will report the stale oneof case as set even though `fields.hasField(...)` for that same field returns false — a direct parallel to Unlock's key-manager pointer surviving a state reset that should have wiped it clean. [6](#0-5) 

The failed invariant: "after `clear()`, the builder must represent the default/empty message for every field, including oneof presence." The attacker-controlled value: the oneof case selected in a first, ordinary bounded parse (an attacker chooses which oneof branch tag appears on the wire). The missing check: `clear()` omits `Arrays.fill(oneofCases, null)` (or equivalent), unlike the hand-written/generated `Builder.clear()` in `GeneratedMessage`/`GeneratedMessageV3`, which does reset oneof case fields as part of its per-field clearing logic.

### Impact Explanation
This is a state/integrity defect in the reflection-based `DynamicMessage` API, not a memory-safety bug. Concrete downstream effects for consuming applications that use `DynamicMessage.Builder` reflectively (a common pattern for generic gateways, protobuf-based RPC frameworks, or any code that pools/reuses a `Builder` across independent request parses for performance) include:
- `hasOneof(oneof)` / `getOneofFieldDescriptor(oneof)` returning a stale field descriptor for a field that has no actual value in the message (`fields.hasField()` is false), causing application logic that branches on "which oneof case is set" (e.g., authorization/dispatch decisions keyed on the oneof discriminator, similar in spirit to Unlock's manager-role check) to make incorrect decisions based on data from a prior, unrelated parse.
- Incorrect merge behavior in `mergeFrom(Message other)`: the stale `oneofCases[i]` entry can cause `fields.clearField(oneofCases[i])` to be invoked against a field that isn't actually set (harmless no-op in that specific sub-case) or can otherwise desynchronize the builder's oneof bookkeeping from its real field content across repeated reuse cycles.

This does not enable memory corruption, RCE, or disclosure of unrelated memory; it is a logic/integrity issue confined to reflective usage of `DynamicMessage`. Severity is bounded by how much a consuming application trusts `DynamicMessage`'s oneof-presence reflection API after builder reuse.

### Likelihood Explanation
Reachable through the ordinary, documented, public `Message.Builder` API (`clear()`, `mergeFrom()`, `parseFrom()`) with fully trusted schemas and bounded, valid wire input — no privileged access or hostile schema required. However, it requires a specific application pattern (reusing/pooling a `DynamicMessage.Builder` instance across multiple independent parses via `clear()` rather than allocating a fresh builder each time, which `DynamicMessage.parseFrom()` itself always does). This makes exploitation depend on how the embedding application uses the reflective API rather than being triggerable purely from `DynamicMessage.parseFrom(...)` alone.

### Recommendation
Reset `oneofCases` in `Builder.clear()`, e.g.:
```java
@Override
public Builder clear() {
  fields = FieldSet.newBuilder();
  unknownFields = UnknownFieldSet.getDefaultInstance();
  Arrays.fill(oneofCases, null);
  return this;
}
```
This mirrors the intent of the Unlock fix recommendation ("reset the manager state regardless of branch") by ensuring all state associated with the previous message's oneof selection is unconditionally cleared, not merely the field-value storage.

### Proof of Concept
Conceptual reproduction using the public API (trusted schema with an existing `oneof`, e.g. `TestOneof2` from `unittest_proto2.proto`):
1. `DynamicMessage.Builder b = DynamicMessage.newBuilder(TestOneof2.getDescriptor());`
2. `b.setField(fooIntField, 42);` → `oneofCases[fooOneofIndex] = fooIntField` and `fields` has `fooIntField` set.
3. `b.clear();` → `fields` is now empty (`fields.hasField(fooIntField)` is `false`), but `oneofCases[fooOneofIndex]` still equals `fooIntField`.
4. Observe: `b.hasOneof(fooOneof)` returns `true` and `b.getOneofFieldDescriptor(fooOneof)` returns `fooIntField`, even though `b.hasField(fooIntField)` returns `false` and `b.build()` produces a message with no fields set — a directly observable state inconsistency reachable purely through the public reflective `Message.Builder` API after `clear()`.

This was traced by direct code reading of `DynamicMessage.java`; no test execution was performed, so the exact downstream behavioral consequence in a specific consuming framework (e.g., which frameworks reuse `DynamicMessage.Builder` via `clear()`) was not verified — that would require testing an actual consuming application.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/DynamicMessage.java (L322-334)
```java
  public static final class Builder extends AbstractMessage.Builder<Builder> {
    private final Descriptor type;
    private FieldSet.Builder<FieldDescriptor> fields;
    private final FieldDescriptor[] oneofCases;
    private UnknownFieldSet unknownFields;

    /** Construct a {@code Builder} for the given type. */
    private Builder(Descriptor type) {
      this.type = type;
      this.fields = FieldSet.newBuilder();
      this.unknownFields = UnknownFieldSet.getDefaultInstance();
      this.oneofCases = new FieldDescriptor[type.toProto().getOneofDeclCount()];
    }
```

**File:** java/core/src/main/java/com/google/protobuf/DynamicMessage.java (L339-344)
```java
    @Override
    public Builder clear() {
      fields = FieldSet.newBuilder();
      unknownFields = UnknownFieldSet.getDefaultInstance();
      return this;
    }
```

**File:** java/core/src/main/java/com/google/protobuf/DynamicMessage.java (L346-372)
```java
    @Override
    public Builder mergeFrom(Message other) {
      if (other instanceof DynamicMessage) {
        // This should be somewhat faster than calling super.mergeFrom().
        DynamicMessage otherDynamicMessage = (DynamicMessage) other;
        if (otherDynamicMessage.type != type) {
          throw new IllegalArgumentException(
              "mergeFrom(Message) can only merge messages of the same type.");
        }
        fields.mergeFrom(otherDynamicMessage.fields);
        mergeUnknownFields(otherDynamicMessage.unknownFields);
        for (int i = 0; i < oneofCases.length; i++) {
          if (oneofCases[i] == null) {
            oneofCases[i] = otherDynamicMessage.oneofCases[i];
          } else {
            if ((otherDynamicMessage.oneofCases[i] != null)
                && (oneofCases[i] != otherDynamicMessage.oneofCases[i])) {
              fields.clearField(oneofCases[i]);
              oneofCases[i] = otherDynamicMessage.oneofCases[i];
            }
          }
        }
        return this;
      } else {
        return super.mergeFrom(other);
      }
    }
```

**File:** java/core/src/main/java/com/google/protobuf/DynamicMessage.java (L480-494)
```java
    @Override
    public boolean hasOneof(OneofDescriptor oneof) {
      verifyOneofContainingType(oneof);
      FieldDescriptor field = oneofCases[oneof.getIndex()];
      if (field == null) {
        return false;
      }
      return true;
    }

    @Override
    public FieldDescriptor getOneofFieldDescriptor(OneofDescriptor oneof) {
      verifyOneofContainingType(oneof);
      return oneofCases[oneof.getIndex()];
    }
```

**File:** java/core/src/main/java/com/google/protobuf/DynamicMessage.java (L528-563)
```java
    @Override
    public Builder setField(FieldDescriptor field, Object value) {
      // This should be kept as long as LazyField is still around. We will use InternalLazyField
      // as the internal details so we should not allow the legacy LazyField to be passed in.
      // TODO: Consider converting from LazyField to InternalLazyField here.
      if (value instanceof LazyField) {
        value = ((LazyField) value).getValue();
      }
      verifyContainingType(field);
      // TODO: This check should really be put in FieldSet.setField()
      // where all other such checks are done. However, currently
      // FieldSet.setField() permits Integer value for enum fields probably
      // because of some internal features we support. Should figure it out
      // and move this check to a more appropriate place.
      verifyType(field, value);
      OneofDescriptor oneofDescriptor = field.getContainingOneof();
      if (oneofDescriptor != null) {
        int index = oneofDescriptor.getIndex();
        FieldDescriptor oldField = oneofCases[index];
        if ((oldField != null) && (oldField != field)) {
          fields.clearField(oldField);
        }
        oneofCases[index] = field;
      } else if (!field.hasPresence()) {
        if (field.isRepeated()
            ? ((List<?>) value).isEmpty()
            : value.equals(field.getDefaultValue())) {
          // Setting a field without presence to its default value is equivalent to clearing the
          // field.
          fields.clearField(field);
          return this;
        }
      }
      fields.setField(field, value);
      return this;
    }
```

**File:** java/core/src/main/java/com/google/protobuf/DynamicMessage.java (L565-577)
```java
    @Override
    public Builder clearField(FieldDescriptor field) {
      verifyContainingType(field);
      OneofDescriptor oneofDescriptor = field.getContainingOneof();
      if (oneofDescriptor != null) {
        int index = oneofDescriptor.getIndex();
        if (oneofCases[index] == field) {
          oneofCases[index] = null;
        }
      }
      fields.clearField(field);
      return this;
    }
```
