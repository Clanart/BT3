## Analog Assessment

The Monero report's core failure is: a validation step that is supposed to gate trust in externally-supplied data (DNSSEC) fails, but the code path that consumes the data does not treat that failure as fatal — the unvalidated value is stored/used as if it had passed validation (CWE-345 / CWE-20). The Protobuf analog I found is in the C++ UTF-8 validation enforcement for `Cord`-backed string fields, where the schema declares `features.utf8_validation = VERIFY` but at least two binary-parsing code paths silently skip the check for the `Cord` representation while still accepting the field as validated.

### Title
Silent UTF-8 validation bypass for `Cord`-typed proto string fields with `utf8_validation = VERIFY` - (File: `src/google/protobuf/generated_message_tctable_lite.cc`)

### Summary
A proto `string` field declared with `features.string_type = CORD` and `features.utf8_validation = VERIFY` is a supported, documented combination [1](#0-0) . `FieldDescriptor::requires_utf8_validation()` reports this field as requiring strict UTF-8 checking [2](#0-1) . However, the tail-call table (`TcParser`) fast/mini-parse path and the classic reflection-based `WireFormat` parse path both accept and store non-UTF-8 bytes into `Cord`-typed string fields without performing the check that the schema requires, so the "verified" invariant is silently dropped for this specific representation.

### Finding Description
`TcParser::MpVerifyUtf8` has two overloads that are dispatched based on the field's storage representation. The `absl::string_view` overload (used for `ArenaStringPtr`/`MicroString` representations) correctly checks `xform_val == field_layout::kTvUtf8` and calls `utf8_range::IsStructurallyValid` [3](#0-2) . The `absl::Cord` overload, used whenever the field's C++ string type is `kCord` [4](#0-3) , ignores `xform_val` entirely and unconditionally returns `true`:

```cpp
bool TcParser::MpVerifyUtf8(const absl::Cord& wire_bytes, ...
                            uint16_t xform_val) {
  switch (xform_val) {
    default:
      ABSL_DCHECK_EQ(xform_val, 0);
      return true;
  }
}
``` [5](#0-4) 

This function is invoked directly in the `kRepCord` branch of `TcParser::MpString`, and its boolean result (`is_valid`) gates whether the parse continues or errors out [6](#0-5) . Since the Cord overload always returns `true`, a field configured with `xform_val == kTvUtf8` never fails validation through this path — the `ABSL_DCHECK_EQ(xform_val, 0)` (compiled out in release/NDEBUG builds) is the only place that would even notice a mismatch, and it is not a substitute for a real check.

The same gap exists in the non-tctable reflection parser: `WireFormat::_InternalParseAndMergeField` sets `strict_utf8_check = field->requires_utf8_validation()` for `TYPE_STRING`, but the `Cord` branch returns immediately after `ctx->ReadCord()` and `reflection->SetString()`, never consulting `utf8_check`/`strict_utf8_check` at all, unlike the adjacent `std::string` branch a few lines below that does perform `WireFormatLite::VerifyUtf8String` [7](#0-6) .

The only place that actually wires in a UTF-8 check for `Cord` fields is the code generator's **serialize** path (`CordFieldGenerator::GenerateSerializeWithCachedSizesToArray` calls `GenerateUtf8CheckCodeForCord`) [8](#0-7) . I was unable to locate an equivalent call on the parse side before running out of investigation budget — the `VerifyUtf8Cord`/`VerifyUTF8CordNamedField` symbol names appear only in `helpers.cc` as arguments to the shared emitter, and no other file references or defines them, which is consistent with the check only being emitted for serialization, not for parsing.

This mirrors the Monero pattern precisely: a piece of metadata (`features.utf8_validation = VERIFY`, analogous to "DNSSEC required") is defined, is honored by one representation (`ArenaStringPtr`, analogous to the CLI path that gates on the check), but a second representation (`Cord`, analogous to the GUI path) accepts and stores the value as if the check had passed even though it was never actually run.

### Impact Explanation
An ordinary client sending a bounded binary Protobuf payload to a message that declares a `Cord`-backed string field with `features.utf8_validation = VERIFY` can smuggle structurally invalid UTF-8 bytes into that field via `ParseFromString`/`MergeFrom`, even though the schema and `FieldDescriptor::requires_utf8_validation()` promise the field is guaranteed valid UTF-8. Consuming application code that relies on this invariant (e.g., feeding the field to UTF-8-only downstream APIs, JSON/text re-encoders, or code that assumes `std::string`/`Cord` contents are safely convertible) can then process attacker-controlled malformed byte sequences under the false assumption they were validated — an integrity-of-content violation analogous to the Monero GUI accepting a DNSSEC-unverified address as though it had been authenticated. It is not a crash or memory-safety bug; it is a silent breach of a documented format guarantee.

### Likelihood Explanation
This is reachable by any ordinary client through the standard public `ParseFrom*`/`MergeFrom*` binary parsing API on a trusted, generated schema that uses `ctype=CORD` together with `features.utf8_validation=VERIFY` — a combination explicitly present in the codebase's own test schema [1](#0-0) , so it is not a contrived or unsupported configuration. No privileged access, hostile schema, or huge/unbounded input is required — only a small, bounded, malformed length-delimited string.

### Recommendation
- Fix `TcParser::MpVerifyUtf8(const absl::Cord&, ...)` to actually branch on `xform_val` and call `utf8_range::IsStructurallyValid` (flattening the Cord or iterating its chunks) when `xform_val == field_layout::kTvUtf8`, matching the `absl::string_view` overload's behavior.
- Fix `WireFormat::_InternalParseAndMergeField`'s `Cord`-bytes/string branch to honor `utf8_check`/`strict_utf8_check` the same way the `std::string` branch does before calling `SetString`.
- Add conformance/unit coverage analogous to `Utf8ValidationTest` (currently only exercised for `ArenaStringPtr`/`std::string` representations) that specifically exercises `Cord`-typed fields with `utf8_validation = VERIFY` through both the tctable and reflection-based parse paths, asserting parse failure on invalid UTF-8.
- Audit `GenerateUtf8CheckCodeForCord` call sites to confirm whether a parse-time invocation exists elsewhere in the generator that I could not locate; if none exists, add one so generated Cord accessors/parsers reject invalid UTF-8 consistently with non-Cord string fields.

### Proof of Concept
Given a trusted schema field equivalent to `unittest.proto`'s `optional_cord` (`string optional_cord = N [ctype=CORD, features.utf8_validation=VERIFY];`) [1](#0-0) :
1. Construct a minimal length-delimited wire payload for that field number containing the single invalid UTF-8 byte `\xFF` (structurally invalid per `utf8_range::IsStructurallyValid`).
2. Call `ParseFromString`/`MergeFrom` on the generated message type via the standard fast tctable path (`TcParser::MpString` → `kRepCord` case) [6](#0-5) .
3. Expected per the field's `VERIFY` feature: parse should fail (`kUpb_DecodeStatus_BadUtf8`/`false`), matching the behavior already proven for the equivalent `ArenaStringPtr`-backed `optional_utf8_string` field.
4. Observed (per code inspection): `MpVerifyUtf8(const absl::Cord&, ...)` unconditionally returns `true` regardless of `xform_val`, so `is_valid` is `true` and parsing succeeds, storing the invalid-UTF-8 bytes into the `Cord` field [5](#0-4) .

I was not able to execute this PoC in this environment (read-only code search); the analysis is based on direct code inspection of the cited functions, and I could not confirm whether a compensating parse-time UTF-8 check for `Cord` fields exists in a codegen path I did not locate. If such a compensating check exists elsewhere, this finding would need to be downgraded or retracted — this uncertainty should be verified with a live build/test run before relying on this report.

### Citations

**File:** src/google/protobuf/unittest.proto (L1838-1840)
```text
  string optional_cord = 536870020 [
    ctype=CORD, features.utf8_validation = VERIFY
  ];
```

**File:** src/google/protobuf/descriptor.cc (L4039-4046)
```text
static bool IsStrictUtf8(const FieldDescriptor* field) {
  return internal::InternalFeatureHelper::GetFeatures(*field)
             .utf8_validation() == FeatureSet::VERIFY;
}

bool FieldDescriptor::requires_utf8_validation() const {
  return type() == TYPE_STRING && IsStrictUtf8(this);
}
```

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L2503-2515)
```text
bool TcParser::MpVerifyUtf8(absl::string_view wire_bytes,
                            const TcParseTableBase* table,
                            const FieldEntry& entry, uint16_t xform_val) {
  if (xform_val == field_layout::kTvUtf8) {
    if (!utf8_range::IsStructurallyValid(wire_bytes)) {
      PrintUTF8ErrorLog(MessageName(table), FieldName(table, &entry), "parsing",
                        false);
      return false;
    }
    return true;
  }
  return true;
}
```

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L2516-2524)
```text
bool TcParser::MpVerifyUtf8(const absl::Cord& wire_bytes,
                            const TcParseTableBase* table,
                            const FieldEntry& entry, uint16_t xform_val) {
  switch (xform_val) {
    default:
      ABSL_DCHECK_EQ(xform_val, 0);
      return true;
  }
}
```

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L2579-2599)
```text
    case field_layout::kRepCord: {
      absl::Cord* field;
      if (is_oneof) {
        field = RefAt<absl::Cord*>(msg, entry.offset);
      } else {
        field = &RefAt<absl::Cord>(base, entry.offset);
      }
      ptr = InlineCordParser(field, ptr, ctx);
      if (!ptr) break;
      is_valid = MpVerifyUtf8(*field, table, entry, xform_val);
      break;
    }

    default:
      Unreachable();
  }

  if (ABSL_PREDICT_FALSE(ptr == nullptr || !is_valid)) {
    PROTOBUF_MUSTTAIL return Error(PROTOBUF_TC_PARAM_NO_DATA_PASS);
  }
  PROTOBUF_MUSTTAIL return ToTagDispatch(PROTOBUF_TC_PARAM_NO_DATA_PASS);
```

**File:** src/google/protobuf/generated_message_tctable_gen.cc (L644-651)
```text
  // Fill in extra information about string and bytes field representations.
  if (field->type() == FieldDescriptor::TYPE_BYTES ||
      field->type() == FieldDescriptor::TYPE_STRING) {
    switch (field->cpp_string_type()) {
      case FieldDescriptor::CppStringType::kCord:
        // `Cord` is always used, even for repeated fields.
        type_card |= fl::kRepCord;
        break;
```

**File:** src/google/protobuf/wire_format.cc (L984-1017)
```text
    case FieldDescriptor::TYPE_STRING:
      utf8_check = true;
      strict_utf8_check = field->requires_utf8_validation();
      ABSL_FALLTHROUGH_INTENDED;
    case FieldDescriptor::TYPE_BYTES: {
      int size = ReadSize(&ptr);
      if (ptr == nullptr) return nullptr;
      if (field->cpp_string_type() == FieldDescriptor::CppStringType::kCord) {
        absl::Cord value;
        ptr = ctx->ReadCord(ptr, size, &value);
        if (ptr == nullptr) return nullptr;
        reflection->SetString(msg, field, value);
        return ptr;
      }
      std::string value;
      ptr = ctx->ReadString(ptr, size, &value);
      if (ptr == nullptr) return nullptr;
      if (utf8_check) {
        if (strict_utf8_check) {
          if (!WireFormatLite::VerifyUtf8String(value.data(), value.length(),
                                                WireFormatLite::PARSE,
                                                field->full_name())) {
            return nullptr;
          }
        } else {
        }
      }
      if (field->is_repeated()) {
        reflection->AddString(msg, field, std::move(value));
      } else {
        reflection->SetString(msg, field, std::move(value));
      }
      return ptr;
    }
```

**File:** src/google/protobuf/compiler/cpp/field_generators/cord_field.cc (L284-296)
```text
void CordFieldGenerator::GenerateSerializeWithCachedSizesToArray(
    io::Printer* p) const {
  auto v = p->WithVars(variables_);
  if (field_->type() == FieldDescriptor::TYPE_STRING) {
    GenerateUtf8CheckCodeForCord(
        p, field_, options_, false,
        absl::Substitute("this_._internal_$0(), ", p->LookupVar("name")));
  }
  p->Emit(R"cc(
    target =
        stream->Write$DeclaredType$($number$, this_._internal_$name$(), target);
  )cc");
}
```
