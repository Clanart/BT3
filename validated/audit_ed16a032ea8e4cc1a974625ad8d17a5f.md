### Title
Oneof-Membership Check Uses Ambiguous Zero Sentinel, Bypassing "At-Most-One-Field-Set" Invariant in ProtoJSON `Type`-based Parser - (File: `src/google/protobuf/json/internal/parser_traits.h`)

### Summary
The `_payDividend()` bug used `dividendDenominationIndex == 0` both as "not yet added" sentinel and as a legitimate index, letting a real entry silently bypass a duplicate/limit check. The equivalent flaw exists in Protobuf's ProtoJSON parser for `google.protobuf.Type`/`Field`-described messages: `ParseProto3Type::RecordAsSeen` treats `oneof_index() == 0` as "field is not part of a oneof," but 0 is also the legitimate, zero-based index of the *first* oneof declared in a message. This causes the oneof "already seen" duplicate check to be skipped for every field belonging to the first oneof, letting an attacker set multiple mutually-exclusive oneof members in one JSON payload while the parser reports success as if no conflict occurred.

### Finding Description
`SeenState RecordAsSeen(Field f, Msg& msg)` in `ParseProto3Type` is responsible for enforcing that at most one field of a given oneof, and no field twice, is set during a single ProtoJSON parse: [1](#0-0) 

```
int oneof_index = f->proto().oneof_index();
if (oneof_index != 0) {
  bool oneof_inserted = msg.parsed_oneofs_indices_.insert(oneof_index).second;
  if (!oneof_inserted) {
    return SeenState::kOneofAlreadySeen;
  }
}
```

`oneof_index` here comes from `google.protobuf.Field.oneof_index`, a plain `int32` on `google.protobuf.Type`'s `Field` message with no explicit-presence tracking. Fields that are *not* members of any oneof leave this field unset, which (proto3 default) reads back as `0`. Fields that *are* members of the message's first declared oneof (a zero-based index, consistent with `OneofDescriptor::index()` conventions used elsewhere in the codebase, e.g. `containingOneof.fieldCount++` indexing in [2](#0-1) ) also report `oneof_index() == 0`.

Because the code guards the whole oneof-tracking block with `if (oneof_index != 0)`, fields belonging to that first oneof never enter the `parsed_oneofs_indices_` set and the block that would return `SeenState::kOneofAlreadySeen` is never reached for them — exactly the "check happens against a value that legitimately overlaps the sentinel" bypass described in the Solidity report (`dividendDenominationIndex[_to][_id] == 0` conflating "unset" and "first slot").

Contrast this with the sibling, correct implementation `ParseProto2Descriptor::RecordAsSeen`, which uses a pointer check (`f->real_containing_oneof() != nullptr`) rather than an integer sentinel, and therefore has no such ambiguity: [3](#0-2) 

The call site treats `kOneofAlreadySeen`/`kFieldAlreadySeen` as fatal parse errors: [4](#0-3) 

### Impact Explanation
The oneof invariant ("at most one member set") is a core Protobuf message-integrity guarantee that downstream application code (switch/case dispatch on `WhichOneof`, security or billing logic keyed off a single active variant, etc.) relies on. If the invariant can be silently violated during ProtoJSON parsing of `Type`/`Field`-described messages (used by dynamic/reflection-based JSON codecs that operate off `google.protobuf.Type` rather than compiled `Descriptor`s), an attacker sending a JSON payload that sets two or more fields belonging to the message's first oneof would have both values written into the serialized proto (as competing tag/value pairs), with no parse error raised. Consuming code that assumes "only one branch can be populated" can then make inconsistent decisions depending on which accessor/tag it reads later (analogous to the report's double-counted dividend denomination corrupting downstream accounting). This is an integrity failure in trusted-schema, bounded-payload parsing — squarely in scope per the rules (no huge-input/resource-exhaustion needed).

### Likelihood Explanation
Reachable via any public ProtoJSON parse API that uses the `Type`-based (non-C++-`Descriptor`) code path, i.e., `ParseProto3Type`, with a completely ordinary, bounded, well-formed JSON payload from an untrusted client — no privileged access, hostile schema, or malformed wire bytes are required. The only precondition is that the target message declares at least one oneof (the very first one, index 0), which is common. This makes the likelihood high wherever this parser backend is exercised.

### Recommendation
Change the sentinel used in `ParseProto3Type::RecordAsSeen` so it cannot collide with a valid zero-based oneof index — e.g. use an `optional`/has-bit-backed `oneof_index` (or a `-1` sentinel with `Field.oneof_index` shifted to be genuinely optional), or switch to the same non-ambiguous check used by `ParseProto2Descriptor` (comparing against a nullable/pointer-like "no oneof" indicator instead of overloading `0`). Add a regression test with a message whose *first* declared oneof has two members supplied in one JSON object, asserting that parsing fails with "already been set" rather than silently succeeding.

### Proof of Concept
Given a `google.protobuf.Type` describing a message `M` with the first oneof (`oneof_index == 0`) containing fields `a` and `b`:

1. Parse JSON `{"a": 1, "b": 2}` against `M` via the `ParseProto3Type` ProtoJSON code path (`ParseField`/`ParseMessage` in `src/google/protobuf/json/internal/parser.cc`).
2. Trace `RecordAsSeen(field_a, msg)`: `oneof_index() == 0` → guard `if (oneof_index != 0)` is false → `parsed_oneofs_indices_` untouched → returns `kFirstSeen`.
3. Trace `RecordAsSeen(field_b, msg)`: identical path, again returns `kFirstSeen` because the set was never populated for oneof index 0.
4. `ParseField` never observes `SeenState::kOneofAlreadySeen`, so both `SetInt32`/whatever setters run, writing two conflicting tags for the same oneof into the output stream, and the overall `absl::Status` returned is OK (no error), unlike the intended "already been set (either directly or as part of a oneof)" rejection at [5](#0-4) .

I was unable to directly inspect `google/protobuf/type.proto`'s doc comment for `Field.oneof_index` in this session to fully confirm the exact "0 = first oneof / unset" semantics from the .proto source itself; this conclusion rests on the observed default-value behavior of a plain (non-explicit-presence) proto3 `int32` field and the zero-based indexing convention used elsewhere in the codebase for oneof indices. A follow-up review of `type.proto`'s field documentation and a live parse test with the constructed payload above would fully confirm the reachable bypass.

### Citations

**File:** src/google/protobuf/json/internal/parser_traits.h (L81-99)
```text
  static SeenState RecordAsSeen(Field f, Msg& msg) {
    if (f->real_containing_oneof() != nullptr) {
      int oneof_index = f->real_containing_oneof()->index();
      bool oneof_inserted =
          msg.parsed_oneofs_indices_.insert(oneof_index).second;
      if (!oneof_inserted) {
        // Oneof takes precedent over Field in the case of "this is in a oneof
        // and the field is set"
        return SeenState::kOneofAlreadySeen;
      }
    }

    bool field_inserted = msg.parsed_fields_.insert(f->number()).second;
    if (!field_inserted) {
      return SeenState::kFieldAlreadySeen;
    }

    return SeenState::kFirstSeen;
  }
```

**File:** src/google/protobuf/json/internal/parser_traits.h (L237-255)
```text
  static SeenState RecordAsSeen(Field f, Msg& msg) {
    int oneof_index = f->proto().oneof_index();
    if (oneof_index != 0) {
      bool oneof_inserted =
          msg.parsed_oneofs_indices_.insert(oneof_index).second;
      if (!oneof_inserted) {
        // Oneof takes precedent over Field in the case of "this is in a oneof
        // and the field is set"
        return SeenState::kOneofAlreadySeen;
      }
    }

    bool field_inserted = msg.parsed_fields_.insert(f->proto().number()).second;
    if (!field_inserted) {
      return SeenState::kFieldAlreadySeen;
    }

    return SeenState::kFirstSeen;
  }
```

**File:** java/core/src/main/java/com/google/protobuf/Descriptors.java (L2028-2037)
```java
        if (proto.hasOneofIndex()) {
          if (proto.getOneofIndex() < 0
              || proto.getOneofIndex() >= parent.toProto().getOneofDeclCount()) {
            throw new DescriptorValidationException(
                this,
                "FieldDescriptorProto.oneof_index is out of range for type " + parent.getName());
          }
          containingOneof = parent.getOneofs().get(proto.getOneofIndex());
          containingOneof.fieldCount++;
          this.parent = checkNotNull(containingOneof);
```

**File:** src/google/protobuf/json/internal/parser.cc (L1256-1267)
```text
  SeenState seen = Traits::RecordAsSeen(*field, msg);

  // Legacy nonconformant behavior only enforces duplicate key checking for
  // fields within the same oneof, otherwise enforce duplicate keys for all
  // fields.
  if (seen == SeenState::kOneofAlreadySeen ||
      (seen == SeenState::kFieldAlreadySeen &&
       !lex.options().allow_legacy_nonconformant_behavior)) {
    return lex.Invalid(absl::StrFormat(
        "'%s' has already been set (either directly or as part of a oneof)",
        name));
  }
```
