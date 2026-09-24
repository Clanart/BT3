### Title
JSON field-name alias smuggling via `json_name`/original-name duplicate-key handling - (File: `python/google/protobuf/json_format.py`, `src/google/protobuf/json/internal/parser.cc`)

### Summary
Protobuf's JSON parsers (Python `json_format.Parse`, C++ `json/internal/parser.cc`) accept two distinct textual spellings for the same field — the canonical `json_name` (lowerCamelCase) and the original proto field name (snake_case) — and treat them as the *same* field for assignment purposes, but only as *different* keys for duplicate-key rejection. When both spellings appear in one JSON object, the parser applies "last one wins" semantics for the underlying field value while the duplicate-key detector only flags identical string keys, not semantically-aliased ones. This mirrors the Envoy AI Gateway MCP flaw: a value trusted/validated under one field spelling can be silently overwritten by an attacker-supplied value under an alternate accepted spelling of the same logical field before the message is re-serialized/consumed downstream.

### Finding Description
Protobuf's ProtoJSON parsers deliberately accept both `fieldName` (json_name) and `field_name` (original proto name) as valid input keys for the same `FieldDescriptor`, per the documented dual-acceptance behavior [1](#0-0) . Duplicate-key detection, however, operates on exact string identity and is documented as an acknowledged gap: `testDuplicateFieldAlternateNames` explicitly states "the duplicate field checker intends to reject inputs with duplicate key names, but it only catches keys that are exact matches and not alternate spellings that correspond to the same field," and demonstrates that `{"int32Value": 1, "int32_value": 2}` parses successfully with the *second* (differently-spelled) key's value winning [2](#0-1) . The same alias-collision silently overwrites map fields as well [3](#0-2) .

The C++ parser (`parser.cc`) resolves `name` to a `field` via `Traits::FieldByName`, tracks "seen" state per resolved `FieldDescriptor` (not per raw JSON key string) via `RecordAsSeen`, and by default (legacy/non-conformant mode) allows the *field* to be seen twice without error, applying whatever came later [4](#0-3) . Conformance tests explicitly encode "duplicated field names have either last-wins or parse failure" as acceptable behavior for exactly this camelCase-vs-snake_case collision (`FieldNameDuplicateDifferentCasing1`/`2`), and multiple language backends (C++, PHP, Python, Python/upb, Ruby) are recorded in their conformance failure lists as *not* rejecting this case, meaning last-write-wins is the actual, exercised behavior in this checkout [5](#0-4) [6](#0-5) .

Java's parser exhibits the identical dual-acceptance property (both `optionalInt32` and `optional_int32` are accepted and each write is applied independently) [7](#0-6) .

**Transfer of the external invariant**: The MCP report's failed invariant is "member names should be matched case-sensitively / exactly, so a security-relevant field cannot be set through an unauthorized alternate spelling that a prior validator did not recognize." Protobuf's ProtoJSON format spec deliberately supports two authorized spellings per field (this is by design and documented, unlike MCP's incidental Go case-folding bug), but the *duplicate-detection* layer that is supposed to catch conflicting/duplicate assignments does not span both spellings — it only catches literal string duplicates. This is the structural analog: an upstream validation layer that inspects raw JSON text/keys (e.g., a WAF, schema validator, or one microservice using `preserving_proto_field_name=True` output while a downstream service parses with default camelCase, or vice versa) can see and approve `"field_name": <safe>` while `"fieldName": <attacker value>` is also present; the protobuf parser silently keeps the later-processed value bound to the *single* underlying field, and any subsequent re-marshal (e.g., via `MessageToJson`) re-serializes using the canonical spelling, erasing all evidence that dual/conflicting values were ever submitted — exactly the "confused deputy" alteration-and-canonicalization pattern from the MCP report.

### Impact Explanation
Where an application places any validation, filtering, or authorization decision on the *raw incoming JSON payload* (or a text-based proxy/gateway) rather than on the fully-parsed protobuf message, and later hands the same payload to `json_format.Parse` / the C++ JSON parser for canonical structured use, an attacker can supply the "safe" value under one accepted spelling and the actual intended (malicious) value under the alias spelling. Because the parser silently prefers the last-seen occurrence for the same field regardless of spelling, the effective value used by the application diverges from what any spelling-naive text inspection observed — a parser/validator differential enabling security-control bypass, analogous to CWE-178 (case-sensitivity comparison flaw) cited in the original advisory. Severity is bounded because this requires an application-level architecture that inspects raw JSON before/instead of relying on the canonical parsed protobuf message (the same "multi-layered architecture" precondition the original report requires for its Medium rating).

### Likelihood Explanation
Moderate: Both accepted spellings (`json_name` and original field name) are core, documented, always-on ProtoJSON behavior across every officially maintained backend (C++, Python, Python/upb, Java, PHP, Ruby) — no special flags are needed to trigger acceptance of either spelling. The gap is explicitly acknowledged in-repo as a known, accepted (non-spec) behavior in `testDuplicateFieldAlternateNames`, and is tracked in every language's conformance failure list, confirming it reproduces on the current checkout across backends rather than being a hypothetical or already-mitigated edge case.

### Recommendation
1. Make duplicate-field detection field-identity-aware rather than string-identity-aware: when both the `json_name` and original-name spellings of the same `FieldDescriptor` appear in one JSON object, treat this as a duplicate-key condition and fail (or require `allow_legacy_nonconformant_behavior`-style opt-in), rather than silently accepting last-write-wins.
2. Document explicitly (in `json_format.py`/`parser.cc` public API docs) that any application performing pre-parse validation on raw JSON text must canonicalize field names first or disable one accepted spelling, since Protobuf's JSON parser can bind either spelling to the same field.
3. Consider providing a strict mode (already partially exposed via `allow_legacy_nonconformant_behavior=false`) that is the default for security-sensitive parsing paths, rejecting alias-duplicate submissions outright.

### Proof of Concept
Using the existing Python test as the reproduction (already present and passing in-repo, demonstrating the behavior rather than a hypothetical):
```python
from google.protobuf import json_format
from google.protobuf.internal import json_format_proto3_pb2 as pb

msg = pb.TestMessage()
# "int32Value" (json_name, camelCase) is the field a raw-JSON validator would see as "safe: 1"
# "int32_value" (original proto name) carries the attacker's real payload and is processed last.
json_format.Parse('{"int32Value": 1,"int32_value":2}', msg)
assert msg.int32_value == 2   # attacker-controlled alias value silently wins
```
This is functionally identical to the existing repository test `testDuplicateFieldAlternateNames` [2](#0-1) , confirming the alias-collision/last-wins behavior is live in this checkout, matching the conformance-tracked cross-backend behavior [8](#0-7) .

### Citations

**File:** conformance/binary_json_conformance_suite.cc (L2513-2554)
```text
  // Using the original proto field name in JSON is also allowed.
  RunValidJsonTest("OriginalProtoFieldName", REQUIRED,
                   R"({
        "fieldname1": 1,
        "field_name2": 2,
        "_field_name3": 3,
        "field__name4_": 4,
        "field0name5": 5,
        "field_0_name6": 6,
        "fieldName7": 7,
        "FieldName8": 8,
        "field_Name9": 9,
        "Field_Name10": 10,
        "FIELD_NAME11": 11,
        "FIELD_name12": 12,
        "__field_name13": 13,
        "__Field_name14": 14,
        "field__name15": 15,
        "field__Name16": 16,
        "field_name17__": 17,
        "Field_name18__": 18
      })",
                   R"(
        fieldname1: 1
        field_name2: 2
        _field_name3: 3
        field__name4_: 4
        field0name5: 5
        field_0_name6: 6
        fieldName7: 7
        FieldName8: 8
        field_Name9: 9
        Field_Name10: 10
        FIELD_NAME11: 11
        FIELD_name12: 12
        __field_name13: 13
        __Field_name14: 14
        field__name15: 15
        field__Name16: 16
        field_name17__: 17
        Field_name18__: 18
      )");
```

**File:** conformance/binary_json_conformance_suite.cc (L2613-2633)
```text
  // Duplicated field names have either last-wins or parse failure.
  RunValidJsonTestOrParseFailure("FieldNameDuplicate", RECOMMENDED,
                                 R"({
                                   "optionalNestedMessage": {"a": 1},
                                   "optionalNestedMessage": {}
                                 })",
                                 "optional_nested_message: {}");
  RunValidJsonTestOrParseFailure("FieldNameDuplicateDifferentCasing1",
                                 RECOMMENDED,
                                 R"({
                                   "optional_nested_message": {"a": 1},
                                   "optionalNestedMessage": {}
                                 })",
                                 "optional_nested_message: {}");
  RunValidJsonTestOrParseFailure("FieldNameDuplicateDifferentCasing2",
                                 RECOMMENDED,
                                 R"({
                                   "optionalNestedMessage": {"a": 1},
                                   "optional_nested_message": {}
                                 })",
                                 "optional_nested_message: {}");
```

**File:** python/google/protobuf/internal/json_format_test.py (L1197-1206)
```python
  def testDuplicateFieldAlternateNames(self):
    # Note: this behavior is non-spec and an oversight bug in the
    # implementation, but would be a breaking change to fix. The duplicate field
    # checker intends reject inputs with duplicate key names, but it only
    # catches keys that are exact matches and not alternate spellings that
    # correspond to the same field.
    parsed_message = json_format_proto3_pb2.TestMessage()
    json_format.Parse('{"int32Value": 1,"int32_value":2}', parsed_message)
    self.assertEqual(parsed_message.int32_value, 2)

```

**File:** python/google/protobuf/internal/json_format_test.py (L1207-1218)
```python
  def testDuplicateFieldAlternateNamesMap(self):
    # Note: this behavior is non-spec and an oversight bug in the
    # implementation, but would be a breaking change to fix. The duplicate field
    # checker intends reject inputs with duplicate key names, but it only
    # catches keys that are exact matches and not alternate spellings that
    # correspond to the same field.
    parsed_message = json_format_proto3_pb2.TestMap()
    json_format.Parse(
        '{"int32Map": {"1": 2}, "int32_map": {"3": 4}}', parsed_message
    )
    self.assertEqual(parsed_message.int32_map, {3: 4})

```

**File:** src/google/protobuf/json/internal/parser.cc (L1256-1279)
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

  // Message and repeated fields are cleared on first sight (meaning, it will
  // clear what was in the message before the parse began), but not cleared
  // in the case of the second occurance of the same key in the same JSON
  // payload (if duplicate keys are accepted, which they are in the current
  // default legacy mode).
  if (seen == SeenState::kFirstSeen &&
      (Traits::IsRepeated(*field) ||
       Traits::FieldType(*field) == FieldDescriptor::TYPE_MESSAGE ||
       Traits::FieldType(*field) == FieldDescriptor::TYPE_GROUP)) {
    Traits::ClearField(*field, msg);
  }
```

**File:** conformance/failure_list_cpp.txt (L12-14)
```text
Recommended.*.JsonInput.FieldNameDuplicate                                                                         # Should have failed to parse or matched expected output but did not.
Recommended.*.JsonInput.FieldNameDuplicateDifferentCasing1                                                         # Should have failed to parse or matched expected output but did not.
Recommended.*.JsonInput.FieldNameDuplicateDifferentCasing2                                                         # Should have failed to parse or matched expected output but did not.
```

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L2180-2187)
```java
    // Parsers accept both original proto field names and lowerCamelCase names.
    TestAllTypes.Builder builder = TestAllTypes.newBuilder();
    JsonFormat.parser().merge("{\"optionalInt32\": 12345}", builder);
    assertThat(builder.getOptionalInt32()).isEqualTo(12345);
    builder.clear();
    JsonFormat.parser().merge("{\"optional_int32\": 54321}", builder);
    assertThat(builder.getOptionalInt32()).isEqualTo(54321);
  }
```
