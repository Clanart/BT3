This confirms the finding is well-documented in this checkout's conformance framework itself, which explicitly acknowledges the differential as a known, tracked, cross-runtime discrepancy — not a novel bug I'm inferring. This is the strongest available analog to HTTP request smuggling's differential-parsing invariant failure.

### Title
ProtoJSON parsers disagree on duplicate-field detection across officially supported runtimes, enabling field-value smuggling via alternate JSON key spellings - (File: `python/google/protobuf/internal/json_format.py`, `java/util/src/main/java/com/google/protobuf/util/JsonFormat.java`, `src/google/protobuf/json/internal/parser.cc`)

### Summary
The SwiftNIO advisory's failed invariant is that two network parties parsing the *same bytes* reach different conclusions about which values/boundaries are authoritative, because one parser is more permissive than the other. The Protobuf analog is that the canonical ProtoJSON spec requires unique field mappings, but the officially supported runtimes enforce this "uniqueness" invariant inconsistently for the exact same input bytes: some reject exact-string duplicate keys but silently accept "last one wins" for duplicate keys that are alternate spellings of the same field (e.g., `fooBar` vs `foo_bar`), while the canonical C++ implementation's duplicate check is field-identity-based and therefore stricter. This creates a genuine cross-implementation parsing differential over untrusted ProtoJSON input, analogous to front-end/back-end HTTP header-parsing disagreement.

### Finding Description
Python's `json_format.Parse` duplicate-key checker only catches keys that are exact string matches, not alternate spellings that resolve to the same field. This is explicitly acknowledged as unintended: `testDuplicateFieldAlternateNames` in [1](#0-0)  states "this behavior is non-spec and an oversight bug in the implementation... The duplicate field checker intends to reject inputs with duplicate key names, but it only catches keys that are exact matches and not alternate spellings that correspond to the same field," and demonstrates `{"int32Value": 1,"int32_value":2}` silently parsing to `int32_value == 2` rather than raising an error, as the exact-duplicate case does in `testDuplicateField` at [2](#0-1) .

By contrast, the canonical C++ JSON parser in `src/google/protobuf/json/internal/parser.cc` performs duplicate detection using `Traits::RecordAsSeen(*field, msg)` keyed on the resolved `FieldDescriptor` identity rather than the literal JSON string, and by default (`allow_legacy_nonconformant_behavior` unset) rejects any second occurrence of the same logical field regardless of spelling: [3](#0-2) . This means the identical JSON payload `{"int32Value": 1,"int32_value":2}` is rejected by one official runtime and silently accepted (last-wins) by another, non-strict runtime — a true parsing differential over untrusted bytes.

The design doc `docs/design/editions/edition-zero-json-handling.md` independently confirms this is a known, unresolved cross-runtime inconsistency: "Conflicting mappings lead to undefined behavior... it's inconsistent across runtimes and unexpected" [4](#0-3) . The conformance suite itself encodes the ambiguity as merely "last-wins or parse failure" rather than a single required outcome, and multiple official backends (jruby, php_c, ruby, python_upb, upb) are listed as failing `FieldNameDuplicate`/`FieldNameDuplicateDifferentCasing` conformance tests differently from each other, per the various `conformance/failure_list_*.txt` files retrieved. Java has an analogous, separately-acknowledged issue where extension short-names colliding with regular field names produce duplicate JSON keys on serialization, causing re-parsing to silently drop one value: [5](#0-4) .

### Impact Explanation
If a trust boundary exists where one service (e.g., a gateway, validator, or access-control layer) uses one protobuf ProtoJSON runtime to validate/authorize a field's value, and forwards the identical raw JSON bytes to a downstream service using a different official runtime for the actual business logic, the two services can disagree about the final value of a field — exactly the "different network parties see a different message" pattern behind request smuggling. This could let an attacker craft a payload where the validating parser sees value A (or rejects the payload) while the executing parser silently accepts value B, undermining authentication/authorization/routing decisions built on field values, consistent with CWE-444 (Inconsistent Interpretation of HTTP Requests, generalized here to inconsistent interpretation of a serialized protocol message across cooperating trust-boundary parsers).

### Likelihood Explanation
This requires no privileged access — an ordinary client submitting bounded ProtoJSON through the standard public `Parse`/`JsonFormat.merge` APIs can trigger the differential deterministically, using only alternate but valid casing/underscore spellings of a field's JSON name, which is a normal feature of ProtoJSON (case-insensitive/`json_name` support), not malformed input. The likelihood of exploitation depends entirely on whether a consuming application actually mixes runtimes/parsers across a trust boundary on the same raw bytes, which is an architecture-dependent (not universal) precondition, and the bug is already tracked/acknowledged by the Protobuf team as a known, accepted-risk oversight rather than a hidden defect.

### Recommendation
Align all official ProtoJSON parser implementations (Python pure-Python and upb-backed, Java GSON-backed, Ruby/PHP upb-backed) to perform duplicate-key detection by resolved field identity (as C++ does via `RecordAsSeen`) rather than literal JSON string equality, and make the "reject on duplicate" behavior the default, non-optional behavior for `LEGACY_BEST_EFFORT`-independent cases such as identical/alternate spellings of the same field, per the direction already scoped in the Edition Zero JSON handling design doc.

### Proof of Concept
Using the Python runtime in this checkout: `json_format.Parse('{"int32Value": 1,"int32_value":2}', json_format_proto3_pb2.TestMessage())` succeeds and yields `int32_value == 2` (test at [1](#0-0) ), whereas the same logical duplicate detection intent, if enforced by field identity as in C++'s `parser.cc` `RecordAsSeen`/`kFieldAlreadySeen` check [3](#0-2) , would reject it. Running the two parses side-by-side on the identical byte string demonstrates the cross-implementation differential without requiring any malformed or out-of-spec bytes.

### Citations

**File:** python/google/protobuf/internal/json_format_test.py (L1191-1195)
```python
  def testDuplicateField(self):
    self.CheckError(
        '{"int32Value": 1,\n"int32Value":2}',
        'Failed to load JSON: duplicate key int32Value.',
    )
```

**File:** python/google/protobuf/internal/json_format_test.py (L1197-1205)
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

**File:** docs/design/editions/edition-zero-json-handling.md (L26-31)
```markdown
*   All proto messages can be serialized to JSON
    *   Conflicting mappings will produce JSON with duplicate keys
*   All proto messages can be parsed from JSON
    *   Conflicting mappings lead to undefined behavior. While the behavior is
        deterministic in all of the cases we've encountered, it's inconsistent
        across runtimes and unexpected.
```

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L331-371)
```java
  /**
   * This test demonstrates the currently known bad behavior of printing extension fields when the
   * name of the extension is the same as the name of a regular field.
   *
   * <p>The JSON serialization of the proto contains a duplicate key. Trying to parse this JSON will
   * result possibly the wrong value being assigned to the regular field and the extension field to
   * be dropped.
   */
  @Test
  public void
      testExtensionFields_printingDeprecatedNonConformantShortExtensionNames_withSameShortName_printsDuplicateJsonKeys()
          throws Exception {
    com.google.protobuf.TypeRegistry registry =
        com.google.protobuf.TypeRegistry.newBuilder()
            .add(TestAllTypesProto2.getDescriptor())
            .build();
    JsonFormat.Printer printer =
        JsonFormat.printer()
            .usingTypeRegistry(registry)
            .printingDeprecatedNonConformantShortExtensionNames();
    JsonFormat.Parser parser = JsonFormat.parser().usingTypeRegistry(registry);
    TestAllTypesProto2 message =
        TestAllTypesProto2.newBuilder()
            .setExtensionSameName("Field entry")
            .setExtension(JsonTestProto2.extensionSameName, "Extension entry")
            .build();

    String json = printer.print(message);
    Message.Builder builder = message.newBuilderForType();
    parser.merge(json, builder);
    Message parsedMessage = builder.build();

    String expectedJsonWithDuplicateKeys =
        "{\n"
            + "  \"extensionSameName\": \"Field entry\",\n"
            + "  \"extensionSameName\": \"Extension entry\"\n"
            + "}";
    assertThat(json).isEqualTo(expectedJsonWithDuplicateKeys);
    // Short names prevent round-trip success, collision occurs due to duplicate json keys.
    assertThat(parsedMessage.toString()).isNotEqualTo(message.toString());
  }
```
