### Title
Unreachable-code assumption in packed-field fallback dispatch can be violated, turning malformed wire input into an `ABSL_LOG(FATAL)` process abort - (File: `src/google/protobuf/wire_format.cc`)

### Summary
`WireFormat::_InternalParseAndMergeField()` (the reflection-based binary parser used by `Message::ParseFrom*`) contains a `switch` on `field->type()` for length-delimited ("packed") wire-type mismatches. The `TYPE_STRING`, `TYPE_GROUP`, `TYPE_MESSAGE`, and `TYPE_BYTES` cases are annotated `ABSL_LOG(FATAL) << "Can't reach";`, on the assumption that `field->is_packable()` already filters these types out before the switch is entered. `ABSL_LOG(FATAL)` aborts the process — this is not a catchable exception, unlike the rest of the parser, which is documented to return `false`/`nullptr` on malformed input rather than crash.

### Finding Description
This is the closest structural analog to the external report: a function whose *contract* is "return an error signal on bad input" (`bool ParseFromString`/`nullptr`-returning `_InternalParse` for the whole `MessageLite` API, as documented at `src/google/protobuf/message_lite.h:430-438` — "If parsing fails (returns false)...") but whose implementation, in the reflection-based fallback path, can hit an unconditional fatal log/abort instead of returning the documented failure value: [1](#0-0) 

The guard is:
```
if (WireFormatLite::GetTagWireType(tag) != WireTypeForFieldType(field->type())) {
  if (field->is_packable() && GetTagWireType(tag) == WIRETYPE_LENGTH_DELIMITED) {
    switch (field->type()) {
      ... numeric HANDLE_PACKED_TYPE cases ...
      case TYPE_ENUM: ...
      case TYPE_STRING:
      case TYPE_GROUP:
      case TYPE_MESSAGE:
      case TYPE_BYTES:
        ABSL_LOG(FATAL) << "Can't reach";
        return nullptr;
    }
  } else {
    return internal::UnknownFieldParse(...);   // graceful path
  }
}
```
The safety of this code hinges entirely on `field->is_packable()` returning `false` for `TYPE_STRING/GROUP/MESSAGE/BYTES`, which is defined in `descriptor.h`/`descriptor.cc` outside this file and not verifiable as a compile-time invariant at this call site — it is an implicit, cross-file, code-reviewer-maintained invariant, exactly the same kind of unenforced assumption that caused the Canto oracle bug (the `Comptroller`'s assumption that `getUnderlyingPrice` never reverts was violated by unchecked calls three call-frames deep).

Contrast this with the rest of this exact function and file, where malformed/mismatched-wiretype input is handled gracefully via `internal::UnknownFieldParse` (line 899) or a `return false`/`return nullptr` (dozens of examples throughout `wire_format.cc`, `parse_context.h`, `message_lite.cc`), and where the equivalent PGO/fast-path C++ TcParser code in `generated_message_tctable_lite.cc` uniformly returns `nullptr`/falls back to `table->fallback` on any decode failure rather than aborting.

### Impact Explanation
If any future change to `FieldDescriptor::is_packable()`, `WireTypeForFieldType()`, or the packed-field encoding rules (e.g., a new field type, an edition/feature interaction, or a regression in the packability classification) ever allows a length-delimited wire type to reach this switch for `STRING`/`GROUP`/`MESSAGE`/`BYTES`, an ordinary client sending a bounded, well-formed-looking Protobuf message (just an unexpected wire-type/field-type combination) would cause the consuming application's process to abort via `ABSL_LOG(FATAL)`. This is a hard, uncatchable crash (not a C++ exception, not a Java/Python exception) — for any consuming server that parses untrusted messages with reflection (`Message::ParseFromString`/`ParseFromArray` on full, non-Lite messages), this is a remote, unauthenticated denial-of-service with no possibility of catch/recover, unlike the normal `return false` contract the rest of the API upholds. This differs from allocation/flooding DoS classes explicitly excluded by the scan rules: it is a logic/assumption bug, not a resource-exhaustion bug.

### Likelihood Explanation
Currently the `is_packable()` guard appears to make this dead code under the present type-classification logic (packable types are exactly the primitive numeric/enum/bool wire types: `VARINT`/`FIXED32`/`FIXED64`), so a normal attacker today likely **cannot** reach this switch arm with today's released classification. This lowers likelihood substantially and is why this is flagged as **Medium at most, contingent** rather than definitively exploitable today — I could not fully trace `FieldDescriptor::is_packable()`'s implementation (only its declaration in `descriptor.h` was located in the index; the body in `descriptor.cc` was not found by search) to give an unconditional 100% proof that no legacy/edition-specific descriptor state (e.g., unusual proto2 group encodings, hand-crafted `FileDescriptorProto`, dynamic message with a corrupted/malicious-but-in-scope schema state) can produce `is_packable()==true` alongside `type()` in `{STRING,GROUP,MESSAGE,BYTES}`. The finding is therefore reported as a defense-in-depth/assumption-fragility bug rather than a demonstrated remote crash with a concrete byte sequence.

### Recommendation
1. Replace the `ABSL_LOG(FATAL) << "Can't reach";` cases with `return internal::UnknownFieldParse(tag, reflection->MutableUnknownFields(msg), ptr, ctx);` (i.e., the same graceful fallback used for the "else" branch just below), so any violation of the `is_packable()` invariant degrades to normal unknown-field handling instead of aborting the process.
2. Add a `static_assert`/compile-time or `ABSL_DCHECK`-then-graceful-return pattern instead of `ABSL_LOG(FATAL)` for reflection-based (not compiler-generated, hence "trusted schema" but still runtime-checked) code paths that process attacker-controlled wire bytes.
3. Add a fuzz/unit regression test that directly calls `_InternalParseAndMergeField` (or exercises it via a `DynamicMessage` with contrived descriptor state) with a length-delimited tag against `TYPE_STRING/GROUP/MESSAGE/BYTES` fields to lock in the fix and catch future regressions of the `is_packable()` classification.

### Proof of Concept
I was not able to construct or run a concrete byte-level reproduction within this environment (no code execution available), and could not fully verify `FieldDescriptor::is_packable()`'s body to prove today's classification can be bypassed — this is stated explicitly per the required proof discipline; no test was run and none should be assumed. The reproduction strategy for a background engineer to validate would be:
1. Build a `DynamicMessage` (or a `FileDescriptorProto`-defined message) with a field of type `TYPE_STRING` (or `MESSAGE`/`BYTES`/`GROUP`).
2. Attempt to drive `field->is_packable()` to `true` for that field (e.g., via a corrupted/forged `FieldDescriptorProto.options.packed=true` combined with atypical type/label combinations, or via edition feature interactions) and confirm via debugger/log whether `WireFormat::_InternalParseAndMergeField` reaches line 894.
3. If reachable, encode a message with a length-delimited tag for that field number and feed it to `Message::ParseFromString()`; observe process abort (`ABSL_LOG(FATAL)`) instead of `false`.

Because reachability could not be confirmed within the current constraints, this should be treated as **Medium severity, unconfirmed exploitability** — a defense-in-depth/robustness gap in the parser's error-handling contract rather than a proven, currently-triggerable remote crash.

### Citations

**File:** src/google/protobuf/wire_format.cc (L835-896)
```text
  if (WireFormatLite::GetTagWireType(tag) !=
      WireTypeForFieldType(field->type())) {
    if (field->is_packable() && WireFormatLite::GetTagWireType(tag) ==
                                    WireFormatLite::WIRETYPE_LENGTH_DELIMITED) {
      Arena* arena = msg->GetArena();

      switch (field->type()) {
#define HANDLE_PACKED_TYPE(TYPE, CPPTYPE, CPPTYPE_METHOD)           \
  case FieldDescriptor::TYPE_##TYPE: {                              \
    ptr = internal::Packed##CPPTYPE_METHOD##Parser(                 \
        reflection->MutableRepeatedFieldInternal<CPPTYPE>(          \
            msg, field,                                             \
            Reflection::GetRepeatedFieldIntent::kHiddenOrInternal), \
        arena, ptr, ctx);                                           \
    return ptr;                                                     \
  }

        HANDLE_PACKED_TYPE(INT32, int32_t, Int32)
        HANDLE_PACKED_TYPE(INT64, int64_t, Int64)
        HANDLE_PACKED_TYPE(SINT32, int32_t, SInt32)
        HANDLE_PACKED_TYPE(SINT64, int64_t, SInt64)
        HANDLE_PACKED_TYPE(UINT32, uint32_t, UInt32)
        HANDLE_PACKED_TYPE(UINT64, uint64_t, UInt64)

        HANDLE_PACKED_TYPE(FIXED32, uint32_t, Fixed32)
        HANDLE_PACKED_TYPE(FIXED64, uint64_t, Fixed64)
        HANDLE_PACKED_TYPE(SFIXED32, int32_t, SFixed32)
        HANDLE_PACKED_TYPE(SFIXED64, int64_t, SFixed64)

        HANDLE_PACKED_TYPE(FLOAT, float, Float)
        HANDLE_PACKED_TYPE(DOUBLE, double, Double)

        HANDLE_PACKED_TYPE(BOOL, bool, Bool)
#undef HANDLE_PACKED_TYPE

        case FieldDescriptor::TYPE_ENUM: {
          auto rep_enum = reflection->MutableRepeatedFieldInternal<int>(
              msg, field,
              Reflection::GetRepeatedFieldIntent::kHiddenOrInternal);
          if (!field->legacy_enum_field_treated_as_closed()) {
            ptr = internal::PackedEnumParser(rep_enum, arena, ptr, ctx);
          } else {
            return ctx->ReadPackedVarint(
                ptr, [rep_enum, field, reflection, msg, arena](int32_t val) {
                  if (field->enum_type()->FindValueByNumber(val) != nullptr) {
                    rep_enum->AddWithArena(arena, val);
                  } else {
                    WriteVarint(field->number(), val,
                                reflection->MutableUnknownFields(msg));
                  }
                });
          }
          return ptr;
        }

        case FieldDescriptor::TYPE_STRING:
        case FieldDescriptor::TYPE_GROUP:
        case FieldDescriptor::TYPE_MESSAGE:
        case FieldDescriptor::TYPE_BYTES:
          ABSL_LOG(FATAL) << "Can't reach";
          return nullptr;
      }
```
