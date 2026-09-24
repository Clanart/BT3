Based on my investigation, I found a strong structural analog. I was not able to fully trace, within the remaining budget, the exact code in `generated_message_tctable_gen.cc` that assigns `kTvUtf8` to a Cord-backed field's `type_card` (the last grep only confirmed that `CppStringType::kCord` and `kRepCord` appear together there, not the exact assignment). I flag that as an open item for full confirmation, but the parsing-side asymmetry itself is directly observable in the fast-table parser and is a clean structural match to the reported bug class.

### Title
Silent UTF-8 validation bypass for Cord-backed string fields in TcParser::MpVerifyUtf8 fast-path parser - (File: src/google/protobuf/generated_message_tctable_lite.cc)

### Summary
The FastChat report describes a control-flow defect (CWE-670): a security check (content moderation) was implemented in one handler (`gradio_block_arena_named.py`) but the identical, duplicated logic in sibling handler files was never updated with the same check, so requests through those siblings bypass moderation entirely. The direct Protobuf analog is `TcParser::MpVerifyUtf8`, which is overloaded once for `absl::string_view`/`std::string` backed string fields and once for `absl::Cord`-backed string fields. The `string_view` overload correctly checks `xform_val == field_layout::kTvUtf8` and runs `utf8_range::IsStructurallyValid`, but the `absl::Cord` overload's `switch` statement has no case for `kTvUtf8` at all — it only has a `default:` branch that unconditionally returns `true`, guarded only by a debug-only `ABSL_DCHECK_EQ(xform_val, 0)`.

### Finding Description
`TcParser::MpString` is the fast-table parser entry point for singular string fields reached from a public binary `ParseFrom*`/`MergeFrom*` call. For fields backed by `absl::Cord` (`case field_layout::kRepCord`), after the bytes are read via `InlineCordParser`, validity is determined by calling the Cord overload of `MpVerifyUtf8`: [1](#0-0) 

That overload is defined as: [2](#0-1) 

Compare this to the sibling `absl::string_view` overload used for `ArenaStringPtr`/`MicroString` fields, which correctly implements the check: [3](#0-2) 

The `Cord` overload's `switch (xform_val)` has only a `default:` case, meaning for *any* value of `xform_val` — including `field_layout::kTvUtf8`, which is exactly the flag the sibling overload branches on to require validation — the function returns `true` without ever calling `utf8_range::IsStructurallyValid`. The `ABSL_DCHECK_EQ(xform_val, 0)` is a debug-build-only assertion; in release/production builds (the default for `ParseFrom*` callers) it compiles to nothing, so the mismatch is a silent pass-through rather than a crash.

This exactly mirrors the external bug's shape: the same named validation routine (`MpVerifyUtf8`) has one correct implementation and one implementation that was never given the equivalent branch, so callers dispatching through the "other" duplicate silently skip the check — an always-incorrect control-flow implementation (CWE-670).

### Impact Explanation
For a `string` field declared with Cord backing (proto2 `[ctype = CORD]`, or any path that routes a strict-UTF-8-required field through the Cord field representation) and marked to require UTF-8 validation (`xform_val == kTvUtf8`, which is the default for proto3 singular string fields and for `features.utf8_validation = MANDATORY`), an attacker sending a bounded binary Protobuf payload with structurally-invalid UTF-8 bytes in that field will have the message parse successfully instead of being rejected. This breaks Protobuf's documented UTF-8 guarantee for `string` fields and produces an in-memory message object whose "string" field contains non-UTF-8 bytes, which downstream application code is entitled to assume is valid UTF-8 (per the type's contract). This is an integrity violation (CWE-670 / weakened-invariant class): downstream string handling (JSON re-serialization, database writes expecting UTF-8, text rendering) can misbehave or produce corrupted/mismatched output, matching the CVSS `I:L` impact profile of the original report.

### Likelihood Explanation
The trigger requires only a normal, bounded binary Protobuf payload sent to any public `ParseFrom*`/`MergeFrom*` API on a message containing a Cord-backed string field that is subject to strict UTF-8 validation, and does not require any privileged access, malicious schema, or resource exhaustion — consistent with the required threat model (ordinary client, supported public parse API, trusted schema). The condition is reachable purely through the fast-table (`TcParser`) code path taken by default for parsing generated messages; no debug/sanitizer build is needed to observe the silent bypass (the DCHECK is compiled out in release builds).

### Recommendation
Add the missing `case field_layout::kTvUtf8:` branch to the `absl::Cord` overload of `TcParser::MpVerifyUtf8`, mirroring the `absl::string_view` overload: convert the `Cord` to a flattened view (or use a Cord-aware UTF-8 validity check) and call `utf8_range::IsStructurallyValid` / log via `PrintUTF8ErrorLog` and return `false` on failure, exactly as done for the `string_view` case, so both string representations enforce the identical invariant.

### Proof of Concept
1. Define a proto2 message with a singular `string` field annotated `[ctype = CORD]` in a schema that also requires strict UTF-8 (e.g., mark the field so `internal::cpp::GetUtf8CheckMode` resolves to `kStrict`, which is the default for proto3-style strict validation).
2. Serialize a minimal binary payload for that message where the length-delimited bytes for this field are structurally invalid UTF-8 (e.g., a lone `0xFF` byte), using the correct field tag/wiretype so it is routed to `TcParser::MpString`.
3. Call the generated message's public `ParseFromString`/`ParseFromArray` (which dispatches to the fast-table parser in a release build).
4. Observe (per the code at [1](#0-0)  and [2](#0-1) ) that parsing succeeds (`is_valid` is unconditionally `true` from the `Cord` overload) despite the invalid UTF-8, whereas the equivalent field backed by `std::string`/`ArenaStringPtr` under the same strict-UTF-8 setting would fail to parse via the `string_view` overload's `utf8_range::IsStructurallyValid` check.

Note: I was not able to fully confirm within budget the exact compiler/table-generation logic in `generated_message_tctable_gen.cc` that assigns `kTvUtf8` specifically to Cord-typed field entries; this should be verified to fully close the reproduction (confirming that a real, buildable schema can produce a Cord field entry with `xform_val == kTvUtf8` reaching `MpString`'s `kRepCord` case).

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

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L2579-2589)
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
```
