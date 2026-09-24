## Analog Found: Missing UTF‑8 status check in `TcParser::MpVerifyUtf8` for Cord-backed string fields

### Title
Silent acceptance of invalid UTF‑8 for `Cord`-represented string fields in the mixed/table-driven binary parser - (File: `src/google/protobuf/generated_message_tctable_lite.cc`)

### Summary
CVE-2024-8096 is a "fail-open on unhandled status" bug: curl's OCSP check only special-cases the `revoked` status and implicitly treats every other non-`revoked` outcome (including `unauthorized`) as "certificate OK," because the validation logic never has an explicit deny-by-default branch. The closest structural analog in this Protobuf checkout is `TcParser::MpVerifyUtf8(const absl::Cord&, ...)`, whose `switch (xform_val)` statement has **only a `default:` case that unconditionally returns `true`**, regardless of what `xform_val` actually is. This is the same "switch/branch collapses every input into the 'valid' outcome instead of positively confirming validity" defect class, just applied to UTF‑8 verification of `string` fields backed by `absl::Cord` instead of certificate-status verification.

### Finding Description
`TcParser::MpVerifyUtf8` has two overloads used by the table-driven ("mixed"/Mp) binary parser when handling `string`-typed fields with `cpp_string_type = CORD` or `= STRING`:

- The `absl::string_view` overload correctly implements a positive check: [1](#0-0) 
It only returns `true` when `xform_val != kTvUtf8`, or when `xform_val == kTvUtf8` **and** `utf8_range::IsStructurallyValid(wire_bytes)` succeeds.

- The `absl::Cord` overload does not implement the equivalent check at all: [2](#0-1) 
The `switch` has no `case field_layout::kTvUtf8:` label — only `default:`, which in C++ matches every possible value of `xform_val`, including `kTvUtf8`. The function therefore always returns `true` and never calls `utf8_range::IsStructurallyValid`. The only "check" present, `ABSL_DCHECK_EQ(xform_val, 0)`, is a debug-only assertion that is compiled out in production/release (`NDEBUG`) builds, so it provides no runtime enforcement.

This function is invoked from the field parser that handles Cord-backed string storage: [3](#0-2) 
`is_valid = MpVerifyUtf8(*field, table, entry, xform_val);` — the exact place where a positive "is this valid?" decision is supposed to be made and propagated into the parse-failure path (`if (!is_valid) ... return Error(...)`).

The invariant that fails to transfer/hold here is the same one in the OCSP bug: a validator that is supposed to enumerate "what counts as valid" and reject everything else, but instead has been reduced to a branch that accepts everything by construction (curl: only `revoked` is bad, everything else assumed good; protobuf: the `switch` has no branch for the "must verify" transform, so every value, including the verify-required one, falls into the accept-everything `default`).

Other parsing paths in this codebase correctly enforce UTF‑8 for the non-Cord representation and for the reflection-based (non-table-driven) parser, confirming that UTF‑8 validation is a real, intended invariant for `VERIFY`-mode string fields, not an accidental no-op: [4](#0-3) 

### Impact Explanation
If this code path is reachable (see Likelihood/uncertainty below), an ordinary client sending a bounded, otherwise well-formed binary Protobuf payload could cause a `string` field declared with `cpp_string_type = CORD` and `features.utf8_validation = VERIFY` (or `strict` proto3 UTF‑8 enforcement) to be populated with bytes that are not valid UTF‑8, even though the application/schema explicitly opted into UTF‑8 enforcement and the parser is supposed to reject such input. This is an integrity failure of the same class as the OCSP bug: the consuming application relies on the parser's "this field is guaranteed valid UTF‑8" contract (e.g., to safely pass the string to UTF‑8-only downstream systems, log sinks, database columns, or other language bindings that assume validated text), and that guarantee is silently violated. It is not a crash, memory-safety, or RCE bug — it is a specification/validation bypass with data-integrity consequences, consistent with a Medium severity rating, analogous to CVSS 6.5 for the original OCSP flaw (integrity impact, no confidentiality/availability impact, network-reachable, no privileges required).

### Likelihood Explanation
Likelihood depends on whether the code generator (`generated_message_tctable_gen.cc`) ever emits `xform_val = kTvUtf8` for a field whose representation is `kRepCord` and whose parsing dispatches through the `Mp*` ("mixed"/general) parser functions rather than the specialized fast-path functions. I was not able to fully confirm this combination is reachable before running out of investigation budget — I found strong circumstantial evidence that `CORD` string fields with `requires_utf8_validation()` are a real, supported combination (the reflection-based `WireFormat::ParseAndMergeField` path explicitly checks `field->requires_utf8_validation()` independent of the C++ string representation), but I could not trace the exact `type_card`/`xform_val` assignment logic in `generated_message_tctable_gen.cc` for `kRepCord` fields within the remaining tool budget. This is the key open question a follow-up investigation must resolve before treating this as a confirmed, exploitable bug rather than dead/defensive code.

### Recommendation
1. In `generated_message_tctable_gen.cc`, confirm whether `kTvUtf8` can be assigned to fields with `kRepCord` representation, and add a regression test with a `cpp_string_type = CORD` field plus `features.utf8_validation = VERIFY` that parses a payload containing invalid UTF‑8 bytes.
2. Fix `TcParser::MpVerifyUtf8(const absl::Cord&, ...)` to mirror the `absl::string_view` overload: add an explicit `case field_layout::kTvUtf8:` that calls `utf8_range::IsStructurallyValid` (Cord has an equivalent structurally-valid check, e.g. via `Cord::TryFlat()` + `utf8_range::IsStructurallyValid`, or iterate chunks), returning `false` and logging via `PrintUTF8ErrorLog` on failure, with `default:` reserved strictly for the "no check" (`xform_val == 0`) case.
3. Replace the `ABSL_DCHECK_EQ` with either a real runtime `ABSL_CHECK`/error return in production builds, or ensure (and test) that the generator provably never emits `kTvUtf8` for `kRepCord` fields, so the invariant is enforced at at least one layer.

### Proof of Concept
I could not run a build/test in this environment (ask-only mode, no filesystem/terminal access), so no executed reproduction is available. A concrete reproduction to be executed by a follow-up engineer:
1. Define a `.proto` message with `string data = 1 [ctype = CORD];` under an edition/feature setting that forces `features.utf8_validation = VERIFY` (or in proto3, which defaults to `VERIFY`), ensuring the compiler routes this field's parsing through `TcParser::MpString` → `case field_layout::kRepCord:` (i.e., not hitting a fast-path/dedicated Cord-parsing function that bypasses `MpVerifyUtf8`).
2. Serialize an invalid-UTF‑8 byte sequence (e.g. `"\xFF"`, as used in `upb/message/utf8_test.cc` and `src/google/protobuf/extension_set_unittest.cc`) into that field's wire bytes directly (bypassing the setter, which does not itself enforce UTF‑8, per the comment in `extension_set_unittest.cc` line 2032-2033) and call `ParseFromString` on a message compiled with this schema.
3. Assert: `ParseFromString` returns `true` (parse "succeeds") and the resulting Cord field contains the invalid `"\xFF"` bytes, in contrast to the expected behavior demonstrated for the `string_view`/`ArenaStringPtr` representation, where `ParseFromString` returns `false` for the same invalid bytes (see `rust/test/shared/utf8/utf8_test.cc` lines 37-51 and `upb/message/utf8_test.cc` lines 44-58, which show `VERIFY`-mode string fields correctly rejecting `"\xff"`). [2](#0-1) [1](#0-0) [5](#0-4)

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
