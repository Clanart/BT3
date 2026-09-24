### Title
Silent UTF-8 validation bypass for Cord-backed string fields in fast-table parser - (File: src/google/protobuf/generated_message_tctable_lite.cc)

### Summary
CVE-2020-9432 stems from a validation helper (`openssl_x509_check_host`) that is supposed to convert an X.509 hostname-check result into a strict boolean but instead propagates a non-boolean/unexpected value through `lua_pushboolean`, so a value that was not actually "match" is coerced into a truthy success signal, defeating the certificate-name check. The transferable invariant is: *a boolean-returning verification gate must fail closed when the underlying check condition it is supposed to enforce is not actually evaluated*.

In this checkout, `TcParser::MpVerifyUtf8` is overloaded for the different in-memory string representations used by the fast wire-format parser tables (`ArenaStringPtr`/`MicroString` and `absl::Cord`) [1](#0-0) . The `absl::string_view` overload correctly branches on `xform_val == field_layout::kTvUtf8` and calls `utf8_range::IsStructurallyValid`, returning `false` on invalid UTF-8 [2](#0-1) . The `absl::Cord` overload, however, does not branch on `xform_val` at all — it unconditionally returns `true` for every `xform_val` value, guarded only by a debug-only `ABSL_DCHECK_EQ(xform_val, 0)`:

```cpp
bool TcParser::MpVerifyUtf8(const absl::Cord& wire_bytes,
                            const TcParseTableBase* table,
                            const FieldEntry& entry, uint16_t xform_val) {
  switch (xform_val) {
    default:
      ABSL_DCHECK_EQ(xform_val, 0);
      return true;
  }
}
``` [3](#0-2) 

This is called from `TcParser::MpString`'s `kRepCord` branch after the wire bytes are read into the Cord, and its boolean result directly gates whether the parse is treated as successful or routed to `Error()`:
```cpp
case field_layout::kRepCord: {
  ...
  ptr = InlineCordParser(field, ptr, ctx);
  if (!ptr) break;
  is_valid = MpVerifyUtf8(*field, table, entry, xform_val);
  break;
}
...
if (ABSL_PREDICT_FALSE(ptr == nullptr || !is_valid)) {
  PROTOBUF_MUSTTAIL return Error(PROTOBUF_TC_PARAM_NO_DATA_PASS);
}
``` [4](#0-3) 

### Finding Description
The `xform_val` parameter encodes whether the field requires strict UTF-8 verification (`field_layout::kTvUtf8`) versus no verification. For the non-Cord overloads, this flag is honored: verification is actually performed and its true/false result reflects the actual UTF-8 structural validity of attacker-supplied bytes [2](#0-1) . For the Cord overload, the function ignores `xform_val` entirely and always reports success (`true`), i.e. "valid," irrespective of whether the field is marked as requiring UTF-8 validation. In release builds (`NDEBUG`), `ABSL_DCHECK_EQ` compiles away, so there is no runtime enforcement that `xform_val` is actually `0` for Cord fields — the function will silently accept invalid UTF-8 for any `kFkString`/`kRepCord` field whose `type_card` carries `kTvUtf8`, exactly mirroring the CVE's pattern of a boolean gate that is supposed to reflect a real check result but instead always yields the "pass" value when the actual verification path was not taken.

### Impact Explanation
If reachable, this allows an attacker sending an otherwise well-formed, bounded Protobuf message through a public `ParseFrom*` API to have a proto3 `string` field (feature `utf8_validation = VERIFY`) that is backed by an `absl::Cord` (`ctype = CORD`) accept structurally invalid UTF-8 bytes without the parser rejecting the message. Downstream code that relies on the language-level guarantee that a `string` field always contains valid UTF-8 (e.g., text encoders, JSON encoders, or application logic doing string operations assuming valid encoding) could then process malformed byte sequences, leading to integrity violations or crashes in code that assumes the invariant holds (this is the same "silent-fail causes a downstream integrity assumption to be broken" pattern as the CVE, not a memory-safety RCE claim).

### Likelihood Explanation
Likelihood cannot be fully confirmed from this checkout. The exploitability of this pattern in production hinges on whether the `CTYPE_CORD` field option can actually coexist with a `string`-typed field where `utf8_validation` resolves to `VERIFY`, i.e., whether the code generator/descriptor validation ever emits a `kRepCord` entry with `xform_val == field_layout::kTvUtf8` set. I was not able to locate, within available tool budget, the exact code path in `generated_message_tctable_gen.cc` or `descriptor.cc` that either permits or forbids this combination at compile/build time. If descriptor validation restricts `ctype = CORD` to `bytes`-typed fields only (which would never require UTF-8 validation), then `MpVerifyUtf8(Cord)` is unreachable dead code with `xform_val != 0`, and this finding would not constitute a real bypass. This uncertainty should be resolved before treating this as a confirmed vulnerability.

### Recommendation
Make `TcParser::MpVerifyUtf8(const absl::Cord&, ...)` actually branch on `xform_val` the same way the `absl::string_view` overload does — performing `utf8_range::IsStructurallyValid` (or Cord-aware equivalent) when `xform_val == field_layout::kTvUtf8`, and returning `false` on invalid UTF-8 — rather than relying on a debug-only assertion that is compiled out in release builds. Additionally, audit whether `ctype = CORD` combined with `utf8_validation = VERIFY` is reachable through the descriptor/build pipeline, and if so, add a regression test analogous to `ExtensionSet.StringWithInvalidUTF8FailsToParse` [5](#0-4)  but using a Cord-backed string field to lock in correct rejection behavior.

### Proof of Concept
I could not produce a verified, runnable reproduction within the current investigation because I was unable to confirm (a) whether a trusted `.proto` schema can generate a `kRepCord` fast-table entry with `xform_val == field_layout::kTvUtf8` set, and (b) the exact generator/descriptor logic that decides this combination. A concrete PoC would require: defining a message with a `string` field with `[ctype = CORD]` under a feature/edition setting that also forces `utf8_validation = VERIFY`, generating C++ code, and calling `ParseFromString` on a serialized message containing invalid UTF-8 (e.g. `"\xFF"`) in that field, then asserting the parse should fail but doesn't. This step was not executed and no test results can be claimed.

**Note on confidence:** This analog is the closest structural match to the CVE's "boolean gate silently returns success without doing the real check" pattern found in this codebase, but its real-world severity is contingent on the unconfirmed reachability question above. If that combination is not reachable, treat this as a defensive-code inconsistency rather than an exploitable Critical/High finding.

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
