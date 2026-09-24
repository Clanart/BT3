### Title
Skipped UTF-8 invariant check for Cord-backed string fields in TcParser fast-path parsing - ([File: src/google/protobuf/generated_message_tctable_lite.cc])

### Summary
The C++ tail-call ("Mp"/mini) fast-path string parser dispatches UTF-8 validation through an overloaded `TcParser::MpVerifyUtf8` helper. The `absl::string_view` overload correctly enforces validation when `xform_val == field_layout::kTvUtf8`, but the sibling `absl::Cord` overload unconditionally returns `true` for every `xform_val`, backed only by a debug-only `ABSL_DCHECK_EQ(xform_val, 0)`. In release builds (the default for consuming applications) the `DCHECK` is compiled out, so a Cord-backed string field configured to require UTF-8 validation silently skips the check while its `ArenaStringPtr`/`MicroString` sibling representations enforce it correctly.

### Finding Description
`TcParser::MpString` dispatches based on the field's `rep` (representation) tag from the field's `type_card`, calling `MpVerifyUtf8(field.Get(), table, entry, xform_val)` after parsing, regardless of the field's underlying storage representation: [1](#0-0) 

For the `kRepAString` (ArenaStringPtr) case, `MpVerifyUtf8` is called on an `absl::string_view` and correctly checks `xform_val == field_layout::kTvUtf8` before invoking `utf8_range::IsStructurallyValid`: [2](#0-1) 

For the `kRepCord` case, the same field's `xform_val` is passed to the `absl::Cord` overload, which never inspects `xform_val` for the `kTvUtf8` value — it only has a `default:` branch that always returns `true`, guarded by a debug-only assertion that `xform_val == 0`: [3](#0-2) 

This mirrors the structure of the external report exactly: an invariant-enforcing helper (`_checkBalances()` in Shell's `EvolvingProteus.sol`, analogous to `MpVerifyUtf8`) is applied consistently on some call paths (`_swap()`/`_lpTokenSpecified()` ≈ the `string_view`/`ArenaStringPtr` path) but is silently omitted on a structurally parallel path that mutates equivalent state (`depositGivenInputAmount()`/`withdrawGivenOutputAmount()` ≈ the `Cord` path). In both cases the omission is not a documented/intended behavioral difference (unlike the legitimate proto2-vs-proto3 UTF-8 leniency distinction verified elsewhere in the codebase, e.g. `upb/message/utf8_test.cc`), but an inconsistency between two code paths that are supposed to enforce the same invariant for the same field configuration.

The `ABSL_DCHECK_EQ(xform_val, 0)` inside the `default:` case is the developer's own acknowledgment that `xform_val` should never be non-zero for Cord fields in the code as currently generated — but this is enforced only via a debug assertion, not a runtime guard, and the function signature/dispatch mechanism does not prevent a `kTvUtf8` value from being routed here if the assumption (that Cord-typed string fields are never marked as requiring UTF-8 validation by the table generator) is violated by any current or future code-generation path, refactor, or a hand-built `TcParseTable` (e.g. constructed via the public table-building APIs used by generated code) that pairs `CppStringType::kCord` with `Utf8CheckMode::kStrict`.

### Impact Explanation
If a `string` field using `CppStringType::kCord` storage is ever marked with `kTvUtf8` in its `type_card` (i.e., configured to require strict UTF-8 validation, as proto3 string fields normally are), a bounded, attacker-controlled binary Protobuf payload containing invalid UTF-8 bytes for that field would be accepted by `ParseFrom*`/`MergeFrom*` in a release build instead of being rejected. This breaks the class-level invariant that all proto3 (or explicitly UTF-8-validated) string fields reject invalid UTF-8 on parse — the same class of invariant-bypass described in the external report, where a bounded, valid-looking client input is accepted despite violating an invariant that sibling code paths correctly enforce. Downstream consumers that rely on the parser having already validated UTF-8 (skipping their own re-validation) could then process/forward corrupted string data, an integrity issue analogous to the pool ratio invariant being silently violated in the Shell report.

### Likelihood Explanation
The `ABSL_DCHECK_EQ(xform_val, 0)` shows this path is currently only reached with `xform_val == 0` in the shipped C++ code generator, so under stock `protoc`-generated code the bug is latent (fixed by generator invariants, not by the parser itself). This differs from the L3 requirement to disprove the analog via upstream checks — here the "check" is an unenforced generator convention plus a compiled-out debug assertion, not a runtime safety net. Because the vulnerability window is the "trusted schema/generated code" boundary (the report's threat model treats generated code as trusted), the realistic likelihood under the stated Protobuf threat model is Low-to-Medium: it requires a hand-crafted or generator-modified `TcParseTable` (not attacker-controlled), rather than exploitation purely through a client-supplied binary payload against a stock, unmodified generated parser. It is nonetheless a genuine missing runtime invariant enforcement — the fix does not rely on trusting the caller.

### Recommendation
Make `TcParser::MpVerifyUtf8(const absl::Cord&, ...)` symmetric with the `absl::string_view` overload: explicitly branch on `xform_val == field_layout::kTvUtf8` and call `utf8_range::IsStructurallyValid` on the Cord's flattened bytes, returning `false` (triggering the parser's `Error` path) on invalid UTF-8, with the `default:`/`DCHECK` reserved strictly for `xform_val == 0`. This removes the reliance on a compiled-out debug assertion as the only guard against a UTF-8-required Cord field silently bypassing validation.

### Proof of Concept
A full end-to-end repro requires constructing a message whose `TcParseTable` pairs `kRepCord` with `kTvUtf8` for a given field (currently not producible via the stock code generator, per the `ABSL_DCHECK_EQ(xform_val, 0)` invariant) and then parsing a payload with an invalid UTF-8 byte sequence (e.g. `"\xFF"`) into that field via `ParseFromString`, and observing that parsing succeeds (returns `true`) instead of failing, in contrast to the equivalent `ArenaStringPtr`-backed field which correctly fails per the existing test `ExtensionSet.StringWithInvalidUTF8FailsToParse`: [4](#0-3) 
I was not able to execute this locally (no filesystem/build access in this mode) or confirm whether any current or in-progress `TcParseTable` construction path (generated or hand-built) actually produces the `kRepCord` + `kTvUtf8` combination; that would need to be verified by a Devin session with repository/build access before treating this as more than a latent/defense-in-depth gap.

### Citations

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

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L2551-2589)
```text
  bool is_valid = false;
  void* const base = MaybeGetSplitBase(msg, is_split, table);
  switch (rep) {
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
    }

    case field_layout::kRepMString: {
      auto& field = RefAt<MicroString>(base, entry.offset);
      ptr = ctx->ReadMicroString(ptr, field, entry.aux_idx, msg->GetArena());
      is_valid = MpVerifyUtf8(field.Get(), table, entry, xform_val);
      break;
    }


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
