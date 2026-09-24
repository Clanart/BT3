## Analog Found: Protobuf JSON Parser Duplicate-Field Detection Bypass via Alternate Name Spelling

### Title
Python `json_format.Parse` duplicate-field rejection bypassed by camelCase/snake_case aliasing, permitting silent last-value overwrite of the same field - (File: `python/google/protobuf/json_format.py`)

### Summary
The Thunderbird CVE's failed invariant is: the parser is supposed to reject/normalize an ambiguous or duplicate identity value, but instead silently resolves to an attacker-controlled value that differs from what other logic (or the human/consuming code) expects, because the duplicate/invalid-value detector only checks one textual representation and not the resolved semantic identity. The Protobuf analog is the pure-Python `json_format.Parse`/`ParseDict` API: its "reject duplicate field" safety check compares raw JSON key strings, not resolved field identity, so an attacker can supply the same field twice under two different valid spellings (`json_name` vs `fields_by_name` key) and defeat the duplicate-detection invariant, causing the last-supplied (attacker-controlled) value to silently overwrite the first.

### Finding Description
In `_ConvertFieldValuePair`, field resolution allows two distinct textual keys to map to the identical `FieldDescriptor` object: [1](#0-0) 

The duplicate-key guard, however, tracks only the raw string `name` that was looked up in the JSON object, not the resolved `field`: [2](#0-1) 

Because `fields_by_json_name` and `message_descriptor.fields_by_name` are two different string->field maps that can both resolve to the same field (e.g. `"int32Value"` -> field via `json_name`, and `"int32_value"` -> the same field via its declared `name`), submitting both keys in one JSON object causes the loop to process the field twice: the `name in names` check never fires because `"int32Value" != "int32_value"` as strings, so no `ParseError` is raised, and the second occurrence's `setattr(message, field.name, value)` silently overwrites the first. This is explicitly acknowledged as unintended behavior in the test suite: [3](#0-2) 

The upstream anti-ambiguity invariant — "a JSON object must not set the same underlying field twice, and if it does, parsing must fail rather than silently pick one value" — is the exact protobuf/ProtoJSON analog of Thunderbird's invariant that an address header must resolve unambiguously to one identity. Both failures stem from a validator that operates on the wrong representation (raw string spelling vs. resolved semantic identity), letting an attacker's second, differently-spelled value silently supersede the first without the caller ever being told a conflict occurred.

### Impact Explanation
Any application built on `google.protobuf.json_format.Parse`/`ParseDict` that relies on "duplicate/conflicting field keys are rejected" to prevent a single JSON payload from smuggling two different values for one field (e.g., validating a field's value early, or expecting the parser to fault on ambiguous input as documented) can be bypassed. An attacker who controls the JSON body sent to such a parsing entry point can supply `{"amount": 10, "amount_value": 999999}`-style pairs (two spellings of one field) so the last, attacker-chosen value silently wins while any code that scans/validates for exact-duplicate keys sees none — a data-integrity/identity-spoofing outcome analogous to Thunderbird trusting the wrong address token. Impact is confined to data integrity within the parsed message (no memory corruption, no RCE); severity is bounded by how the consuming application uses the "no duplicate fields" guarantee.

### Likelihood Explanation
High reachability: any caller of the public `google.protobuf.json_format.Parse`/`ParseDict` API with a trusted schema and attacker-controlled, bounded JSON input hits this path directly — no special privileges, custom schema, or malicious plugin needed. It reproduces deterministically for any field whose `json_name` differs from its declared `name` (true for essentially all non-single-word field names, which is the common case).

### Recommendation
Change the duplicate-detection in `_ConvertFieldValuePair` to key off the resolved `field` (e.g. `field.full_name` or `id(field)`/`field.number`) rather than the raw JSON key string `name`, so that any two JSON keys resolving to the same `FieldDescriptor` are treated as a conflict and raise `ParseError`, matching the C++/`upb` JSON parser's behavior which already tracks "seen" state per resolved field via `RecordAsSeen`: [4](#0-3) 

### Proof of Concept
Using the existing test fixture `TestMessage` (proto3) from `json_format_test.py`, the following demonstrates the bypass (mirrors the already-present regression test acknowledging the bug):
```python
from google.protobuf import json_format
from google.protobuf.internal import test_util  # or any pb2 with int32_value field

msg = json_format_proto3_pb2.TestMessage()
# Two different spellings of the SAME field ("int32_value"), no ParseError raised:
json_format.Parse('{"int32Value": 1, "int32_value": 2}', msg)
assert msg.int32_value == 2  # second, attacker-controlled value silently wins
```
This is confirmed directly by the pre-existing test `testDuplicateFieldAlternateNames`, which asserts exactly this silent overwrite occurs and documents it as an oversight bug: [5](#0-4) 
Contrast with the correctly-rejected exact-duplicate case (`testDuplicateField`), which raises `ParseError` only because the raw strings happen to match exactly: [6](#0-5)

### Citations

**File:** python/google/protobuf/json_format.py (L604-630)
```python
    fields_by_json_name = dict(
        (f.json_name, f) for f in message_descriptor.fields
    )

    def _ClearFieldOrExtension(message, field):
      if field.is_extension:
        message.ClearExtension(field)
      else:
        message.ClearField(field.name)

    def _GetFieldOrExtension(message, field):
      if field.is_extension:
        return message.Extensions[field]
      else:
        return getattr(message, field.name)

    def _SetFieldOrExtension(message, field, value):
      if field.is_extension:
        message.Extensions[field] = value
      else:
        setattr(message, field.name, value)

    for name in js:
      try:
        field = fields_by_json_name.get(name, None)
        if not field:
          field = message_descriptor.fields_by_name.get(name, None)
```

**File:** python/google/protobuf/json_format.py (L663-671)
```python
        if name in names:
          raise ParseError(
              'Message type "{0}" should not have multiple '
              '"{1}" fields at "{2}".'.format(
                  message.DESCRIPTOR.full_name, name, path
              )
          )
        names.append(name)
        value = js[name]
```

**File:** python/google/protobuf/internal/json_format_test.py (L1191-1195)
```python
  def testDuplicateField(self):
    self.CheckError(
        '{"int32Value": 1,\n"int32Value":2}',
        'Failed to load JSON: duplicate key int32Value.',
    )
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

**File:** src/google/protobuf/json/internal/parser_traits.h (L78-99)
```text
  // Marks a field as having been "seen". This will return a SeenState
  // indicating whether it is first seen, duplicate field seen, or oneof
  // conflict seen.
  static SeenState RecordAsSeen(Field f, Msg& msg) {
    if (f->real_containing_oneof() != nullptr) {
      int oneof_index = f->real_containing_oneof()->index();
      bool oneof_inserted =
          msg.parsed_oneofs_indices_.insert(oneof_index).second;
      if (!oneof_inserted) {
        // Oneof takes precedent over Field in the case of "this is in a oneof
        // and the field is set"
        return SeenState::kOneofAlreadySeen;
      }
    }

    bool field_inserted = msg.parsed_fields_.insert(f->number()).second;
    if (!field_inserted) {
      return SeenState::kFieldAlreadySeen;
    }

    return SeenState::kFirstSeen;
  }
```
