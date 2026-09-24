## Analysis

The TLS report's failed invariant is structural, not certificate-specific: a security check (`validate_keyusage`) was implemented for one code path (`handshake_client.ml` TLS 1.2 `answer_certificate_RSA`/`_DHE`) and never ported to a newer, structurally parallel path (`handshake_client13.ml` `answer_certificate`) that handles the same conceptual operation. The check exists in the codebase, is provably correct on one path, and is silently absent on the sibling path — an attacker-controlled input (a cert lacking the right EKU) sails through the path that lacks the check.

The strongest transferable Protobuf analog is exactly this "duplicate-implementation parity gap" pattern applied to the invariant "wire bytes bound to a `string` field must be validated as structurally valid UTF-8 (`features.utf8_validation = VERIFY` / proto3 semantics)." This check is enforced on the `ArenaStringPtr`/`MicroString` backing-store overload of `TcParser::MpVerifyUtf8` but is not implemented on the `absl::Cord` backing-store overload used for the same logical field type in the same dispatcher (`TcParser::MpString`). [1](#0-0) 

### Title
Missing UTF-8 validation for Cord-backed string fields in TcParser::MpVerifyUtf8 (table-driven fast parser) - (File: src/google/protobuf/generated_message_tctable_lite.cc)

### Summary
`TcParser::MpString`, the table-driven ("mini") parser used by generated C++ message parsing (`ParseFromString`/`MergeFrom` on a trusted, compiler-generated schema), dispatches UTF-8 verification through an overloaded helper, `MpVerifyUtf8`. The `absl::string_view` overload correctly checks `xform_val == field_layout::kTvUtf8` and rejects structurally invalid UTF-8 with `utf8_range::IsStructurallyValid`. The `absl::Cord` overload — used when the field's backing storage is `ctype=CORD` — has a `switch` statement containing only a `default:` case that unconditionally returns `true`, guarded only by `ABSL_DCHECK_EQ(xform_val, 0)`, a check compiled out in release (`NDEBUG`) builds. [2](#0-1) 

### Finding Description
`MpString`'s `kRepCord` case reads the length-delimited wire bytes into the `absl::Cord` field via `InlineCordParser` and then calls the same `MpVerifyUtf8(*field, table, entry, xform_val)` used by the string/arena-string case to gate acceptance: [3](#0-2) 

Compare this to the sibling `kRepAString` case, which performs the identical call pattern against the `absl::string_view` overload that *does* enforce the check: [4](#0-3) 

The `absl::string_view` overload is the one with a real invariant enforcement:

```
2503|bool TcParser::MpVerifyUtf8(absl::string_view wire_bytes, ... ) {
2506|  if (xform_val == field_layout::kTvUtf8) {
2507|    if (!utf8_range::IsStructurallyValid(wire_bytes)) {
2510|      return false;
2511|    }
2512|    return true;
2513|  }
2514|  return true;
2515|}
```

The `absl::Cord` overload is the parity gap:

```
2516|bool TcParser::MpVerifyUtf8(const absl::Cord& wire_bytes, ... ) {
2519|  switch (xform_val) {
2520|    default:
2521|      ABSL_DCHECK_EQ(xform_val, 0);
2522|      return true;
2523|  }
2524|}
``` [5](#0-4) 

The `entry.type_card`/`xform_val` scheme is exactly the mechanism the reflection-based (non-table) path uses to decide whether `field->requires_utf8_validation()` is true — i.e., for proto3 `string` fields (or any field with `features.utf8_validation = VERIFY`) the compiler-generated type card carries `field_layout::kTvUtf8`, and this value is passed regardless of the field's backing storage (`ArenaStringPtr`, `MicroString`, or `Cord`). The reflection-based path enforces this uniformly for the same class of field: [6](#0-5) 

That the `Cord` overload takes `xform_val` as a parameter and switches on it at all shows the intended design mirrors the `string_view` overload; the `kTvUtf8` case was simply never implemented for the `Cord` storage kind. This is structurally identical to the TLS report: `validate_keyusage` was correctly wired for `answer_certificate_RSA`/`answer_certificate_DHE` (TLS 1.2) but never ported to `answer_certificate` (TLS 1.3) — a security check present on one storage/handshake variant of an otherwise-shared operation, absent on a sibling variant of the exact same conceptual operation, reachable by an ordinary client through the standard public parse API with no privileged access, hostile schema, or resource exhaustion required.

### Impact Explanation
Protobuf documents and enforces (via `RejectInvalidUtf8` conformance requirements — see `conformance/failure_list_python.txt` entries for `RejectInvalidUtf8.String.*`) that proto3 `string` fields (and proto2/editions fields with `utf8_validation=VERIFY`) must reject non-UTF-8 wire bytes at parse time. Downstream consuming applications rely on this guarantee: string fields are assumed by the *entire ecosystem* (JSON/text serialization, logging, security filters that treat "string" as an implicit UTF-8-safety boundary, HTML/log-injection defenses, C APIs marshalling to `char*`) to always contain valid UTF-8 once parsing succeeds. When the field uses `ctype=CORD` and the mini table-driven parser reaches `MpString`'s `kRepCord` branch, arbitrary attacker-controlled bytes — including invalid UTF-8 sequences, unpaired surrogates, and overlong encodings — are accepted into a `string`-typed field without validation in release builds, silently violating that platform-wide invariant. Any consuming application or serializer that trusts "protobuf string fields are validated UTF-8" (e.g., re-encoding to JSON, downstream systems doing string comparisons across encodings, or embedding in contexts sensitive to encoding confusion) inherits corrupted/invalid data it believed was validated. This is a genuine integrity failure of a documented parse-time guarantee, reachable purely by sending crafted bounded binary protobuf through the standard public parse API against a trusted, valid schema — matching the report's threat model (ordinary client, bounded input, trusted schema/generated code).

### Likelihood Explanation
Reaching this code requires only: (1) a schema field declared as a `string` (or edition-equivalent) with `ctype = CORD`, and (2) UTF-8 validation applicable to that field (proto3 `string`, or explicit `features.utf8_validation = VERIFY`). Both are ordinary, supported, documented schema configurations — no hostile schema or plugin is needed, matching the "trusted schema/valid schema" constraint in scope. The generated table for such a field carries `kTvUtf8` in its `type_card`, so `MpString`'s `kRepCord` branch is reached on ordinary parsing of any message containing that field via the standard table-driven ("mini"/TcTable) parser used by generated `ParseFromString`/`MergeFrom`. In release builds (`NDEBUG`, the default for production deployments), `ABSL_DCHECK_EQ` is compiled out, so the bypass is fully silent with no crash and no error signaled to the caller — a pure release-only silent-acceptance bug, which raises rather than lowers likelihood of going unnoticed in production versus a debug-build assertion failure.

### Recommendation
Implement the `kTvUtf8` case in `TcParser::MpVerifyUtf8(const absl::Cord&, ...)` identically to the `absl::string_view` overload: flatten/iterate the `Cord`'s contents (or use a Cord-aware `utf8_range::IsStructurallyValid` equivalent) and reject the field the same way the `string_view` path does, calling `PrintUTF8ErrorLog` and returning `false` on failure. Add a conformance/unit test parallel to `ExtensionSet.StringWithInvalidUTF8FailsToParse` and `upb/message/utf8_test.cc` that specifically exercises a `ctype=CORD` string field with invalid UTF-8 through the table-driven `TcParser::MpString` fast path (not just the reflection-based `WireFormat::ParseAndMergeField` path), to prevent future path-parity regressions between backing-store implementations.

### Proof of Concept
Conceptual reproduction (schema, trusted; input, attacker-controlled bytes):

```proto
syntax = "proto3";
message M {
  string s = 1 [ctype = CORD];
}
```

Wire bytes for field 1 (`tag = (1<<3)|2 = 0x0A`), length 1, payload `0xFF` (not valid UTF-8 under any encoding):

```
0x0A 0x01 0xFF
```

Expected (per conformance policy and `string_view`/`ArenaStringPtr` path): `M::ParseFromString(data)` returns `false` / throws `InvalidProtocolBufferException`-equivalent, matching behavior verified for non-Cord string fields in `CheckUtf8Test.testParseRequiredStringWithBadUtf8` and `ExtensionSet.StringWithInvalidUTF8FailsToParse`. [7](#0-6) 

Actual (reasoned from code, not executed — I do not have a code-execution tool to run this build in this session): when the field's `type_card` routes through `TcParser::MpString`'s `kRepCord` branch (release build), `MpVerifyUtf8(const absl::Cord&, ...)` hits its unconditional-`true` `default:` branch regardless of `xform_val == kTvUtf8`, so `is_valid` is set `true` and parsing succeeds with the invalid byte `0xFF` stored in `M.s`.

**Verification caveat**: I confirmed the code-level parity gap by direct inspection of `generated_message_tctable_lite.cc` and cross-referenced the type-card/`xform_val` mechanism against the reflection-based enforcement in `wire_format.cc` and the `field_layout::kTvUtf8` constants declared in `generated_message_tctable_impl.h`. I was not able to execute an actual build/parse in this environment to empirically confirm the runtime behavior (no terminal/build tool available in ask-only mode), and I was not able to fully trace the compiler-side logic (`generated_message_tctable_gen.cc`, `compiler/cpp/helpers.cc`) that decides whether `ctype=CORD` fields are ever assigned `kTvUtf8` in `type_card` versus routed to a different validation mechanism (e.g., a check that occurs earlier, outside `MpString`, gating out this configuration entirely). If the code generator never emits `kTvUtf8` for `CppStringType::kCord` fields, this code path is dead/defensive rather than reachable, and the finding would be weakened to an unreachable-code hardening gap rather than an exploitable bypass. Confirming generator-side reachability would require building the compiler and generating code for a `ctype=CORD` proto3 string field, which needs a Devin session with full build/execution access to verify empirically.

### Citations

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L2503-2524)
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

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L2554-2568)
```text
    case field_layout::kRepAString: {
      auto& field = RefAt<ArenaStringPtr>(base, entry.offset);
      Arena* arena = msg->GetArena();
      if (arena) {
        ptr = ctx->ReadArenaString(ptr, &field, arena);
      } else {
        std::string* str = field.MutableNoCopy(nullptr);
        ptr = InlineGreedyStringParser(str, ptr, ctx);
      }
      if (ABSL_PREDICT_FALSE(ptr == nullptr)) {
        EnsureArenaStringIsNotDefault(msg, &field);
        break;
      }
      is_valid = MpVerifyUtf8(field.Get(), table, entry, xform_val);
      break;
```

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L2579-2590)
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
```

**File:** src/google/protobuf/wire_format.cc (L522-532)
```text
        bool strict_utf8_check = field->requires_utf8_validation();
        std::string value;
        if (!WireFormatLite::ReadString(input, &value)) return false;
        if (strict_utf8_check) {
          if (!WireFormatLite::VerifyUtf8String(value.data(), value.length(),
                                                WireFormatLite::PARSE,
                                                field->full_name())) {
            return false;
          }
        } else {
        }
```

**File:** src/google/protobuf/extension_set_unittest.cc (L2017-2040)
```text
TEST(ExtensionSet, StringWithInvalidUTF8FailsToParse) {
  // Sanity check that the extension field is marked as requiring UTF-8
  // validation. `requires_utf8_validation` can only be true for string fields
  // where feature.utf8_validation = VERIFY.
  const google::protobuf::DescriptorPool* pool = google::protobuf::DescriptorPool::generated_pool();
  ASSERT_NE(pool, nullptr);
  const google::protobuf::FieldDescriptor* string_ext_fd = pool->FindExtensionByName(
      "proto2_unittest.optional_utf8_string_extension");
  ASSERT_NE(string_ext_fd, nullptr);
  ASSERT_TRUE(string_ext_fd->requires_utf8_validation());
  std::string invalid_utf8 = "\xFF";
  ASSERT_FALSE(utf8_range::IsStructurallyValid(invalid_utf8));

  proto2_unittest::TestUtf8ValidationOfExtensions test_message;
  // It is reasonable to debate that UTF-8 validation should be checked in the
  // setter, but it is not currently done because the setter doesn't have a way
  // to report errors.
  test_message.SetExtension(proto2_unittest::optional_utf8_string_extension,
                            invalid_utf8);
  std::string data;
  ASSERT_TRUE(test_message.SerializeToString(&data));
  proto2_unittest::TestUtf8ValidationOfExtensions parsed_message;
  ASSERT_FALSE(parsed_message.ParseFromString(data));
}
```
