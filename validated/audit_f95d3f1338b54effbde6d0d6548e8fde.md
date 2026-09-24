### Title
JSON name collision between an extension field and a regular field with the same short name causes ambiguous/duplicate keys, letting one value silently overwrite or drop the other on parse - (File: `java/util/src/main/java/com/google/protobuf/util/JsonFormat.java`)

### Summary
The vLLM report is a **CWE-436 Interpretation Conflict**: an upstream component (Pillow) accepts input in one canonical semantic (EXIF-rotated / tRNS-transparent pixels) while a downstream consumer (the model) is handed a differently-interpreted flattening of the same bytes, producing operator/consumer disagreement about what the "real" content is. The transferable invariant is: *"two components that should agree on the meaning of the same serialized data can, because a conversion/normalization step is skipped or ambiguous, disagree — and the disagreement is silent, not an error."*

In Protobuf's JSON codec, the closest analog is the Java `JsonFormat` printer's `printingDeprecatedNonConformantShortExtensionNames()` option combined with the default JSON parser's lenient handling of duplicate/legacy field names. When a regular field and an extension happen to share the same *short* JSON name, the printer legitimately emits **two JSON object members with an identical key**. Standard JSON semantics (and most JSON parsers, including the GSON-based reader used internally) resolve duplicate keys by "last one wins," so re-parsing that JSON silently drops one of the two logically distinct protobuf values and assigns the other — with no error, no exception, and no indication to the caller that data was lost or reattributed.

### Finding Description
`JsonFormat.Printer.printingDeprecatedNonConformantShortExtensionNames()` prints extension field names in their short (non-fully-qualified) form instead of the conformant `[fully.qualified.name]` bracket syntax. If an ordinary field and an extension declared on the same message happen to share that short name, the printer's JSON output contains two members with the exact same key string, e.g.:

```
{
  "extensionSameName": "Field entry",
  "extensionSameName": "Extension entry"
}
``` [1](#0-0) 

This is a genuine, currently-shipping (non-test-only) production code path: the printer option and the resulting duplicate-key output are produced by `JsonFormat.java`'s extension-name printing logic, not by a test harness fabricating malformed input. The comment in the test explicitly documents this as "currently known bad behavior."

On the parse side, Java's `JsonFormat.Parser` (backed by GSON) does **not** reject duplicate JSON member names by default; it keeps the last occurrence, silently overwriting the earlier field/extension value: [2](#0-1) 

Other language/implementation JSON parsers in the same codebase show the identical divergence pattern is a recurring, known class rather than an isolated bug:
- The canonical protobuf JSON parser's field-lookup/duplicate-detection logic (`RecordAsSeen`) explicitly special-cases a "legacy nonconformant" mode that *only* enforces duplicate-key rejection within a oneof, leaving ordinary duplicate field names to be silently accepted under "last one wins": [3](#0-2) 
- The Python `json_format.Parse` duplicate-key checker only catches *exact* string matches and misses alternate spellings (`camelCase` vs `snake_case`) that refer to the same field, so `{"int32Value": 1, "int32_value": 2}` silently resolves to `2` with no diagnostic — the same "same semantic slot, different textual identity" ambiguity: [4](#0-3) [5](#0-4) 
- Documentation for the wire/JSON presence model explicitly acknowledges that JSON, being "semantically unordered" with a "unique member name" requirement, cannot unambiguously express "last one wins" for `oneof`/duplicate scenarios, confirming this is a known, structural interpretation gap rather than a corner-case bug: [6](#0-5) 

The invariant that fails is the same one that failed in vLLM: **a producer emits data under one interpretation (two semantically distinct values, "field" and "extension"), and a consumer collapses/reinterprets it under another interpretation (single JSON key, last-value-wins), with no explicit signal that information was discarded or misattributed.**

### Impact Explanation
This is a data-integrity / interpretation-conflict issue, matching CWE-436 and the CVSS profile of the vLLM finding (`C:N/I:L/A:L`). Concretely:
- A message printed with `printingDeprecatedNonConformantShortExtensionNames()` can have its extension value silently discarded and replaced by the colliding regular field's value (or vice versa) upon re-parse, with `parsedMessage.toString()` provably diverging from the original `message.toString()`: [7](#0-6) 
- Any application that treats "extension present" as a security- or policy-relevant signal (e.g., an authorization extension, a feature flag, an audit marker) could have that signal silently dropped or overwritten by an unrelated regular field of the same short name, with the round trip appearing successful (no thrown exception).
- Because printing this format is opt-in but not fenced behind any additional collision check, a trusted schema containing a coincidentally-colliding extension/field pair will deterministically reproduce the corruption for every affected message — this is not an attacker-controlled fuzzing artifact, it is a structural collision in the printer/parser pairing.

Severity is Medium: it requires a specific opt-in printer configuration plus a schema-level name collision (not attacker-controlled at the wire level, and it does not itself crash or leak memory), but the integrity consequence (silent field/extension corruption on JSON round trip) directly parallels vLLM's "model input silently diverges from operator expectation."

### Likelihood Explanation
Moderate-to-low but non-negligible:
- Requires the deprecated printer option `printingDeprecatedNonConformantShortExtensionNames()` to be enabled, which the API and its Javadoc mark as "deprecated" precisely because of this issue — meaning it is a real, reachable code path in production configurations that still enable it for legacy compatibility.
- Requires a schema where an extension short name collides with a regular field's JSON name (a schema-design condition, not an attacker-injected payload) — this is realistic in codebases with many independently-declared extensions sharing common field name conventions across a large `.proto` corpus.
- No memory-safety exploit or malicious peer is needed — purely a trusted-schema, ProtoJSON round-trip using a publicly documented, currently-shipping API surface, exactly matching the "ordinary client through a supported public parse API" threat model.

### Recommendation
- Detect and reject duplicate JSON member names produced by `printingDeprecatedNonConformantShortExtensionNames()` at print time (fail loudly instead of emitting colliding keys), or disallow the option entirely when a collision between an extension short name and a regular field name is detected in the message's descriptor.
- On the parse side, make duplicate-key rejection unconditional (not gated behind `allow_legacy_nonconformant_behavior`) for any field/extension collision, matching the stricter behavior already used for `RepeatedOneofKeys`/`FieldNameDuplicate`-style checks elsewhere in the JSON parser.
- Extend the Python-style alternate-name duplicate checker so that camelCase/snake_case aliases of the same field are also flagged as duplicates rather than silently resolved by last-value-wins, closing the same class of ambiguity across all language implementations.

### Proof of Concept
Existing repository test (production API, not a synthetic harness) already demonstrates the corruption end-to-end: [8](#0-7) 

1. Build a `TestAllTypesProto2` message that sets both `extension_same_name` (regular field) and the `extensionSameName` extension to different string values.
2. Print it with `JsonFormat.printer().usingTypeRegistry(registry).printingDeprecatedNonConformantShortExtensionNames()` — the output JSON contains two members named `"extensionSameName"` with different values (`"Field entry"` and `"Extension entry"`).
3. Re-parse that JSON with `JsonFormat.parser().usingTypeRegistry(registry).merge(json, builder)`. The parse succeeds (no exception) but `parsedMessage.toString()` is asserted to be `isNotEqualTo(message.toString())` — proving the extension value is silently dropped/overwritten, an interpretation conflict between what was serialized and what a JSON consumer materializes back, with no error signal.

### Citations

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

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L902-929)
```java
  @Test
  public void testNullLastInDuplicateOneof() throws Exception {
    TestOneof.Builder builder = TestOneof.newBuilder();
    mergeFromJson("{\"oneofInt32\": 1, \"oneofNestedMessage\": null}", builder);
    TestOneof message = builder.build();
    assertThat(message.getOneofInt32()).isEqualTo(1);
  }

  @Test
  public void testParserRejectDuplicatedFields() throws Exception {
    // TODO: The parser we are currently using (GSON) will accept and keep the last
    // one if multiple entries have the same name. This is not the desired behavior but it can
    // only be fixed by using our own parser. Here we only test the cases where the names are
    // different but still referring to the same field.

    // Duplicated optional fields.
    try {
      TestAllTypes.Builder builder = TestAllTypes.newBuilder();
      mergeFromJson(
          "{\n"
              + "  \"optionalNestedMessage\": {},\n"
              + "  \"optional_nested_message\": {}\n"
              + "}",
          builder);
      assertWithMessage("expected exception").fail();
    } catch (InvalidProtocolBufferException e) {
      // Exception expected.
    }
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

**File:** python/google/protobuf/internal/json_format_test.py (L1197-1218)
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

**File:** docs/field_presence.md (L94-100)
```markdown
-   Because JSON elements are unordered, there is no way to unambiguously
    interpret the "last one wins" rule.
    -   In most cases, this is fine: JSON elements must have unique names:
        repeated field values are not valid JSON, so they do not need to be
        resolved as they are for TextFormat.
    -   However, this means that it may not be possible to interpret `oneof`
        fields unambiguously: if multiple cases are present, they are unordered.
```
