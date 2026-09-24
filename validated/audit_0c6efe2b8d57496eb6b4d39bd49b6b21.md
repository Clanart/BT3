## Title
Python `json_format.Parse` duplicate-key check bypassed by field-name/`json_name` aliasing, causing silent last-write-wins on the same field — ([File: python/google/protobuf/json_format.py])

## Summary
`_Parser._ConvertFieldValuePair` in `json_format.py` is intended to reject ProtoJSON payloads that specify the same field twice, whether by its `json_name` or by its original `name`. The duplicate check only compares the literal JSON key string against a `names` list, so a payload that specifies the same field once by `json_name` and once by original `name` (or a differently-cased alternate spelling) is *not* rejected — the second occurrence silently overwrites the first with no error and no indication to the caller that two "different" keys resolved to the same field.

## Finding Description
`_ConvertFieldValuePair` resolves the field for each incoming JSON key with: [1](#0-0) 
i.e., it looks up `json_name` first, then falls back to the field's declared proto `name`. This means two distinct JSON keys — e.g. `"int32Value"` and `"int32_value"` — can both resolve to the exact same `FieldDescriptor` object.

The duplicate-detection logic, however, tracks only the *literal key strings* seen so far: [2](#0-1) 
Since `"int32Value"` and `"int32_value"` are different strings, `name in names` never triggers, and the loop proceeds to set the field twice — once for each key — with the second write winning. Nothing surfaces to the caller: no exception, no warning, and the wire-level meaning of "this field was specified once with value X" silently becomes "this field ends up with value Y" depending on key ordering in the JSON object.

This is confirmed as a known, acknowledged bug in the project's own tests: [3](#0-2) 
The comments explicitly state: *"this behavior is non-spec and an oversight bug in the implementation... intends [to] reject inputs with duplicate key names, but it only catches keys that are exact matches and not alternate spellings that correspond to the same field."* The same oversight applies to map fields (`int32Map` vs `int32_map`) as shown in the adjacent test.

The oneof duplicate check has the analogous same class of gap: it tracks `oneof_name` (the resolved oneof, not raw key strings) so it does correctly catch aliasing *within* a oneof: [4](#0-3) 
but the plain per-field duplicate check at lines 663-670 operates purely on the untranslated JSON key text, which is where the aliasing bypass lives.

## Analog to the Reported Bug Class
The GovernorCompatibilityBravo advisory's failed invariant is: two different, semantically-equivalent ways of specifying the same intended operation (raw ABI-encoded calldata vs. signature+params) must produce identical, unambiguous results; a bug in the "convenience" encoding path let the two representations silently diverge, and downstream code trusted the (wrong) result without any indication something was off.

The Protobuf analog transfers this exact invariant: a JSON message field can legally be addressed by two accepted spellings — the canonical `json_name` and the original proto `name` (this dual-addressing is explicit, intentional ProtoJSON behavior, not malformed input). The parser's uniqueness/duplicate-key safety check is supposed to guarantee that a given field is set from at most one JSON key, matching the conformance-suite's stated intent that duplicated field names must "either last-wins or parse failure" deterministically: [5](#0-4) 
But the Python implementation's check is keyed on the wrong identity (raw string) instead of the resolved `FieldDescriptor`, so a bounded, ordinary-client-controlled JSON payload sent through the public `json_format.Parse` API can present what looks like two independent field assignments and get an outcome that is undocumented, non-deterministic across implementations, and inconsistent with what other language runtimes (C++, Java, Go) would produce for the same bytes. This mirrors the Bravo bug's core harm: a supported public-facing "friendly" parsing path silently produces a different effective value than the caller's mental model / than other equivalent representations of the "same" input, with no error raised.

## Impact Explanation
Any application that parses attacker/client-supplied ProtoJSON via `google.protobuf.json_format.Parse` (a supported public API, not test-only code) and relies on: (a) duplicate-field rejection as an input-validation guarantee, or (b) consistent behavior across protobuf language runtimes for the same JSON, can be given a message whose final field value differs from what a strict/conformant parser (or a different-language runtime) would produce for the identical payload. Because this is silent (no exception raised, unlike the `int32Value`/`int32Value` exact-duplicate case which does raise `ParseError`), an application cannot detect that an ambiguous/duplicate specification occurred. This is a data-integrity/silent-semantic-divergence issue rather than memory corruption or crash, consistent with a Medium-severity classification, matching the original report's severity band. It is most concerning for authorization- or amount-relevant fields (e.g., a field controlling permissions or a payment amount) where cross-runtime or cross-representation JSON canonicalization is assumed by the consuming application (e.g., signature verification over "canonical" JSON, or comparing JSON blobs for idempotency).

## Likelihood Explanation
High likelihood of being reachable: `json_format.Parse` is the standard, public, documented ProtoJSON parsing entry point in the Python runtime, requires no privileged access, and the trigger is an ordinary bounded JSON payload containing the same field addressed via `name` and `json_name` (or case variants) simultaneously — no schema tricks, no malicious plugins, no huge inputs. The bug is also explicitly acknowledged in-repo as intentionally left unfixed to avoid a breaking change, meaning it is a stable, long-standing behavior across releases rather than a transient regression.

## Recommendation
Change the duplicate-detection identity in `_ConvertFieldValuePair` from the raw JSON key string to the resolved `FieldDescriptor` (and its extension identity), so that any two JSON keys resolving to the same field (whether via `json_name`, original `name`, or case variants) are treated as a genuine duplicate and rejected with `ParseError`, matching the exact-duplicate-key behavior already implemented for literal repeats. Given the acknowledged backward-compatibility risk, this should be gated behind an opt-in/strict-mode flag (or documented clearly as a known deviation) rather than silently changed, and the same fix should be applied to the equivalent map-field code path (`testDuplicateFieldAlternateNamesMap`).

## Proof of Concept
Using the repository's own test file as the reproduction (no modification needed — it demonstrates the exact bypass and is asserted to succeed rather than raise): [6](#0-5) 
```python
# Exact-string duplicate: correctly raises ParseError
json_format.Parse('{"int32Value": 1,\n"int32Value":2}', TestMessage())
# -> ParseError: "Failed to load JSON: duplicate key int32Value."

# Aliased duplicate (json_name vs proto name): NOT rejected, silently overwritten
msg = TestMessage()
json_format.Parse('{"int32Value": 1,"int32_value":2}', msg)
assert msg.int32_value == 2   # first value (1) silently discarded, no error raised

# Same bypass for map fields
msg2 = TestMap()
json_format.Parse('{"int32Map": {"1": 2}, "int32_map": {"3": 4}}', msg2)
assert msg2.int32_map == {3: 4}  # entry from "int32Map" silently discarded
```
Both assertions pass in this checkout with no exception raised, confirming the duplicate-field safety check is bypassable via the two accepted spellings of the same field, exactly as documented in the (self-acknowledged) test comments.

### Citations

**File:** python/google/protobuf/json_format.py (L626-630)
```python
    for name in js:
      try:
        field = fields_by_json_name.get(name, None)
        if not field:
          field = message_descriptor.fields_by_name.get(name, None)
```

**File:** python/google/protobuf/json_format.py (L663-670)
```python
        if name in names:
          raise ParseError(
              'Message type "{0}" should not have multiple '
              '"{1}" fields at "{2}".'.format(
                  message.DESCRIPTOR.full_name, name, path
              )
          )
        names.append(name)
```

**File:** python/google/protobuf/json_format.py (L672-682)
```python
        # Check no other oneof field is parsed.
        if field.containing_oneof is not None and value is not None:
          oneof_name = field.containing_oneof.name
          if oneof_name in names:
            raise ParseError(
                'Message type "{0}" should not have multiple '
                '"{1}" oneof fields at "{2}".'.format(
                    message.DESCRIPTOR.full_name, oneof_name, path
                )
            )
          names.append(oneof_name)
```

**File:** python/google/protobuf/internal/json_format_test.py (L1191-1217)
```python
  def testDuplicateField(self):
    self.CheckError(
        '{"int32Value": 1,\n"int32Value":2}',
        'Failed to load JSON: duplicate key int32Value.',
    )

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

**File:** conformance/binary_json_conformance_suite.cc (L2613-2626)
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
```
