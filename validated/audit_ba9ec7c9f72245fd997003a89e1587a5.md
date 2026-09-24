## Analog Found: Silent duplicate-key acceptance across alternate spellings in Python ProtoJSON parser

### Title
Duplicate-key detection bypassed by alternate spelling silently overwrites field data - (File: `python/google/protobuf/json_format.py`)

### Summary
The Jenkins CVE-2018-1000863 failed invariant is: a single logical identity (a username) has multiple textual representations, the storage-migration code did not canonicalize/deduplicate across these representations before writing, and an attacker-chosen name silently clobbered another user's stored record. The structural analog in this Protobuf checkout is the Python ProtoJSON parser's duplicate-field-name check, which is applied only at the raw-JSON-key level (`_DuplicateChecker`) before field resolution, while the actual field binding used by `_ConvertFieldValuePair` accepts multiple distinct spellings (`json_name`, i.e. camelCase, and the raw proto `name`, i.e. snake_case) that resolve to the *same* field. Because the per-key "already seen" tracking in `_ConvertFieldValuePair` (`names` list, lines 663-670) is keyed on the literal JSON key string rather than the resolved `FieldDescriptor`, two differently-spelled keys mapping to the same field bypass duplicate detection and the second spelling silently overwrites the first, with no error raised.

### Finding Description
`json_format.Parse` first loads JSON text via `json.loads(text, object_pairs_hook=_DuplicateChecker)` [1](#0-0) , and `_DuplicateChecker` only rejects **exact, literal** duplicate key strings [1](#0-0) . It has no knowledge of the schema, so it cannot detect that `"int32Value"` and `"int32_value"` are two spellings of the same field.

Later, `_ConvertFieldValuePair` resolves each JSON key to a `FieldDescriptor` by first checking `fields_by_json_name` (camelCase) and falling back to `message_descriptor.fields_by_name` (snake_case) [2](#0-1) . Its own re-entry guard against duplicates only appends the **literal key string** `name` to the `names` list and checks `if name in names` [3](#0-2) , not the resolved `field` object. Consequently, `{"int32Value": 1, "int32_value": 2}` passes both checks (different literal keys) yet both entries assign to the exact same underlying field, and the second occurrence silently overwrites the first via `setattr(message, field.name, value)` [4](#0-3) .

This is not a hypothetical: it is admitted and explicitly regression-tested as a known bug in the codebase itself: [5](#0-4) 
```
def testDuplicateFieldAlternateNames(self):
  # Note: this behavior is non-spec and an oversight bug in the
  # implementation, but would be a breaking change to fix. The duplicate field
  # checker intends reject inputs with duplicate key names, but it only
  # catches keys that are exact matches and not alternate spellings that
  # correspond to the same field.
  ...
  json_format.Parse('{"int32Value": 1,"int32_value":2}', parsed_message)
  self.assertEqual(parsed_message.int32_value, 2)
```
The same bypass reproduces for map fields (`testDuplicateFieldAlternateNamesMap`) [6](#0-5) .

The C++/upb JSON parser has the analogous but intentionally-tolerant behavior gated by an explicit "legacy nonconformant" flag: `SeenState::kFieldAlreadySeen` is only rejected `!lex.options().allow_legacy_nonconformant_behavior` [7](#0-6) , and conformance test suites explicitly document `FieldNameDuplicateDifferentCasing1/2` as accepted-or-failing "either last-wins or parse failure" [8](#0-7) , with these tests appearing in known failure lists across bindings (`upb`, Python, Ruby) [9](#0-8) . This shows the project treats "last-one-wins across alternate spellings" as a known, tolerated deviation from the JSON RFC's uniqueness requirement — but the Python pure-Python path additionally advertises (via `_DuplicateChecker`) that it rejects duplicate keys, when in fact it only rejects *literal* duplicates, creating a false sense of protection for any consuming application relying on `ParseError` for duplicate-key rejection.

### Impact Explanation
For a consuming application that treats a successful `json_format.Parse` (with no `ParseError`) as proof there were no duplicate/conflicting keys in the input (a common assumption, since the API explicitly implements `_DuplicateChecker` for that exact purpose), an attacker who controls one ProtoJSON payload can supply two differently-cased spellings of the same field. The parse succeeds without error, and the second spelling silently wins — potentially overwriting a value the application logic expected to be protected from modification (e.g., an authorization flag, owner id, or similar single-source-of-truth field) purely due to a spelling variant the application's validation logic didn't anticipate. This mirrors the Jenkins CVE's core failure mode: silent, attacker-influenced overwrite of data due to incomplete canonicalization of alternate name spellings, causing integrity impact without any memory-safety violation.

### Likelihood Explanation
The attacker only needs to be an ordinary client sending a bounded, well-formed ProtoJSON payload through the public `google.protobuf.json_format.Parse`/`ParseDict` API — no privileged access, malicious schema, or crafted binary wire format is required. The behavior is 100% deterministic and already demonstrated by the project's own unit tests, so likelihood of triggering is high; the only prerequisite is that some consuming application relies on `json_format.Parse` raising `ParseError` for duplicate keys as an integrity guarantee.

### Recommendation
Change the per-field duplicate check in `_ConvertFieldValuePair` to key off the resolved `FieldDescriptor` object rather than the literal JSON key string, so that any two JSON keys resolving to the same field (via `json_name` or `name`) are treated as duplicates and rejected consistently, matching the intent already documented in `_DuplicateChecker`. Alternatively, explicitly document in the public API that `json_format.Parse`'s duplicate-key protection does not cover alternate-spelling collisions, so applications do not rely on it as an integrity guarantee.

### Proof of Concept
Using the trusted `TestMessage` schema already present in the repository's test suite:
```python
from google.protobuf import json_format
from google.protobuf.internal import json_format_proto3_pb2

msg = json_format_proto3_pb2.TestMessage()
json_format.Parse('{"int32Value": 1,"int32_value":2}', msg)
assert msg.int32_value == 2  # second spelling silently overwrote the first; no ParseError raised
```
This exact reproduction is already codified as `testDuplicateFieldAlternateNames` in `python/google/protobuf/internal/json_format_test.py` (lines 1197-1206), confirming the behavior is reproducible and acknowledged rather than speculative.

### Citations

**File:** python/google/protobuf/json_format.py (L432-438)
```python
def _DuplicateChecker(js):
  result = {}
  for name, value in js:
    if name in result:
      raise ParseError('Failed to load JSON: duplicate key {0}.'.format(name))
    result[name] = value
  return result
```

**File:** python/google/protobuf/json_format.py (L620-624)
```python
    def _SetFieldOrExtension(message, field, value):
      if field.is_extension:
        message.Extensions[field] = value
      else:
        setattr(message, field.name, value)
```

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

**File:** python/google/protobuf/internal/json_format_test.py (L1197-1217)
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

**File:** conformance/failure_list_python.txt (L1-2)
```text
Recommended.*.JsonInput.FieldNameDuplicateDifferentCasing1                                                         # Should have failed to parse or matched expected output but did not.
Recommended.*.JsonInput.FieldNameDuplicateDifferentCasing2                                                         # Should have failed to parse or matched expected output but did not.
```
