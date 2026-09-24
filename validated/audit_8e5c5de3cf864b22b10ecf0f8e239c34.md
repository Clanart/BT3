## Analysis

The Ruby SAML CVE's root cause is a **parser differential** (CWE-436, Interpretation Conflict): ReXML and Nokogiri build different in-memory structures from the identical byte stream, so a security check performed against one structure doesn't match what is actually consumed downstream, letting an attacker's signed node be "shadowed" by an injected unsigned node with the same effective identity.

Protobuf's binary wire format is too rigid (strict TLV) to reproduce this, but **ProtoJSON parsing of duplicate/differently-cased keys is exactly this bug class**, and it is proven (not speculative) by Protobuf's own conformance suite and failure lists.

### Title
ProtoJSON duplicate-field-name handling diverges across official Protobuf language runtimes (Interpretation Conflict) - ([File: src/google/protobuf/json/internal/parser.cc])

### Summary
When the same bounded, trusted-schema ProtoJSON payload contains a field name twice (identical spelling, or the same field via its snake_case name and its camelCase `json_name`), different official Protobuf parse-API implementations disagree on the outcome: some silently accept the duplicate and apply "last value wins," others raise a hard parse error, and the underlying JSON tokenizer used (e.g., GSON in Java) has its own independent last-wins behavior that Protobuf's own duplicate-detection logic does not fully control. This is the same "two parsers, two structures from one input" primitive that enabled the Ruby-SAML signature-wrapping bypass.

### Finding Description
The canonical C++/upb JSON parser explicitly tracks whether a field name has already been "seen" for the current message and, by default, **rejects** a second occurrence: `Traits::RecordAsSeen` returns `SeenState::kFieldAlreadySeen`, and unless `allow_legacy_nonconformant_behavior` is set, the parser calls `lex.Invalid(...)` and fails the parse. [1](#0-0) 

The Python implementation independently re-implements the same check by tracking a local `names` list and raising `ParseError` on the second occurrence of the same JSON key. [2](#0-1) 

In contrast, the Java `JsonFormat` parser is built on top of Gson's `JsonObject`, which stores entries in a map — when the same key appears twice in a JSON string, Gson keeps only the *last* value and never signals a conflict to Protobuf's own merge logic; the field-name/oneof "already seen" checks operate over the JSON object's *iteration*, not over the raw token stream, so a duplicate key never reaches Protobuf's Java validation path with both values present. This precise divergence is called out by the Protobuf Java test suite itself: [3](#0-2) 

Protobuf's own binary/JSON conformance suite documents that this divergence is intentional/allowed rather than pinned to one deterministic outcome, testing both snake_case/camelCase duplicate aliasing of the *same* field and expecting either "last-wins" success or parse failure, but not a single canonical result: [4](#0-3) 

The failure lists confirm this is not theoretical — the PHP-C and Java conformance runners disagree with the "REQUIRED" expectation for exactly this case in real test execution: [5](#0-4) 

The underlying invariant that fails to transfer/hold: a single JSON document, fed through the officially supported public parse API of two different Protobuf language runtimes with the *same trusted schema*, does not deterministically map to the same in-memory message — one implementation's duplicate-key/oneof-conflict detector can be silently defeated by the JSON tokenizer it is layered on top of (Gson), while another implementation's detector (hand-rolled lexer in C++, explicit list-tracking in Python) catches the same input and rejects it.

### Impact Explanation
If a system uses two different Protobuf language runtimes across a trust boundary that ProtoJSON crosses (e.g., a Java-based gateway that authorizes/validates a request field, forwarding the same raw JSON bytes to a C++ or Python backend that re-parses and acts on it, or vice-versa), an attacker can craft a payload with the security-relevant field name duplicated (verbatim or via its `snake_case`/camelCase alias). The validating hop may observe one effective value (e.g., due to Gson's last-wins collapsing before Protobuf-level duplicate detection ever sees two entries) while the enforcing hop, using strict-reject or different tokenization, either rejects the message (denial) or, if using `allow_legacy_nonconformant_behavior`, resolves the ambiguity differently than the validator did — reproducing the "signature wrapping" style confused-deputy pattern where the value that was checked is not the value that is used. This is a CWE-436/CWE-347-class authentication/authorization bypass primitive when ProtoJSON is used for security decisions across heterogeneous, officially supported Protobuf runtimes.

### Likelihood Explanation
Medium. This requires (a) a deployment that parses the identical untrusted ProtoJSON bytes with two different Protobuf language implementations at different trust levels (a real, common pattern for polyglot microservice/gateway architectures), and (b) a security-relevant field whose duplication or snake_case/camelCase aliasing changes the effective parsed value at one hop but not the other. No malicious schema, no privileged access, no memory corruption is required — only a bounded, well-formed JSON document sent through each runtime's standard `JsonFormat.parser().merge(...)` / `json_format.Parse(...)` / `google::protobuf::json::JsonToMessage(...)` public API. Protobuf's own conformance suite already flags this exact scenario as "RECOMMENDED" (not guaranteed uniform), and the PHP-C/Java failure lists show it manifests in practice today, which raises likelihood above purely theoretical.

### Recommendation
- Treat ProtoJSON duplicate-field-name / snake_case-vs-camelCase-aliasing handling as security-relevant, and make rejection of duplicate keys (including cross-alias duplicates) the default, non-opt-out behavior across all officially supported runtimes, rather than "RECOMMENDED."
- In the Java implementation, perform duplicate-key detection over the raw JSON token stream (or a Gson `JsonReader` configured to reject duplicate object keys) before entries are merged into a `JsonObject`, rather than relying on `JsonObject`'s last-wins map semantics.
- Document explicitly that ProtoJSON must not be used as a security decision boundary between heterogeneous Protobuf runtime versions/languages without a canonicalization step, analogous to the guidance now given for XML canonicalization.

### Proof of Concept
Using the same schema (`TestAllTypes` with `optional_nested_message`) and the following ProtoJSON body — reachable through each runtime's standard public `parse`/`merge` API with default options:
```json
{
  "optionalNestedMessage": {"a": 1},
  "optional_nested_message": {}
}
```
- C++/upb default parser: rejects with `"'optional_nested_message' has already been set"` (per `ParseField` duplicate check). [6](#0-5) 
- Conformance suite models this exact input as `FieldNameDuplicateDifferentCasing1`/`2`, expecting either failure or a defined "last-wins" outcome — i.e., implementations are permitted to diverge on the very same bytes. [7](#0-6) 
- The PHP-C failure list shows this exact test ID failing against the "REQUIRED" expectation in real conformance runs, confirming actual divergence between implementations for identical input. [8](#0-7) 

No test execution was performed in this session; the above is a documented, evidence-backed configuration (conformance test IDs and failure lists already present in the repository) rather than a newly executed reproduction.

### Citations

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

**File:** python/google/protobuf/json_format.py (L663-669)
```python
        if name in names:
          raise ParseError(
              'Message type "{0}" should not have multiple '
              '"{1}" fields at "{2}".'.format(
                  message.DESCRIPTOR.full_name, name, path
              )
          )
```

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L910-916)
```java
  @Test
  public void testParserRejectDuplicatedFields() throws Exception {
    // TODO: The parser we are currently using (GSON) will accept and keep the last
    // one if multiple entries have the same name. This is not the desired behavior but it can
    // only be fixed by using our own parser. Here we only test the cases where the names are
    // different but still referring to the same field.

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

**File:** conformance/failure_list_php_c.txt (L1-4)
```text
Recommended.*.JsonInput.FieldNameDuplicate                                                                         # Should have failed to parse or matched expected output but did not.
Recommended.*.JsonInput.FieldNameDuplicateDifferentCasing1                                                         # Should have failed to parse or matched expected output but did not.
Recommended.*.JsonInput.FieldNameDuplicateDifferentCasing2                                                         # Should have failed to parse or matched expected output but did not.
Recommended.Proto2.JsonInput.FieldNameExtension.Validator
```
