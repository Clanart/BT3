### Title
ProtoJSON alias-name smuggling defeats duplicate-field rejection in pure-Python parser - (File: `python/google/protobuf/json_format.py`)

### Summary
Traefik's flaw is that ForwardAuth's identity-header protection tracks a header by its literal wire name, while the backend collapses multiple distinct-but-equivalent spellings (`X-Foo`, `X_Foo`, `X.Foo`) into one variable, letting a smuggled alias survive the "one canonical value" invariant and silently override the asserted value. The Python implementation of `google.protobuf.json_format` has the same class of bug: its duplicate-field guard tracks the literal JSON key string, not the resolved `FieldDescriptor`, so a client that sends both the `json_name` spelling and the original `name` spelling of the same field (two different, both-valid, non-colliding JSON keys) bypasses the "message should not have multiple X fields" check and the second occurrence silently overwrites the first with no error.

### Finding Description
`JsonFormat.Parser._ConvertFieldValuePair` iterates the parsed JSON object and resolves each key to a field via `fields_by_json_name` or `fields_by_name`: [1](#0-0) 

Duplicate-field protection is then keyed off the raw JSON string, not the resolved `field` object: [2](#0-1) 

Because `json.loads` only rejects two occurrences of the *identical* string key (verified by `testDuplicateField`), a payload containing both the camelCase `json_name` (e.g. `"int32Value"`) and the original snake_case field name (e.g. `"int32_value"`) survives JSON-level dedup as two distinct keys, and each resolves to the same `FieldDescriptor` via `fields_by_json_name`/`fields_by_name`. Since `names` only ever contains the literal strings seen so far, `name in names` never matches, the "should not have multiple fields" `ParseError` is never raised, and the second key's value silently overwrites the first via `_ConvertAndSetScalar`/`setattr`. This exact behavior is acknowledged in the test suite as an "oversight bug" that is intentionally left unfixed for backward compatibility: [3](#0-2) 

This mirrors the Traefik invariant failure precisely: a consumer-side canonicalization/uniqueness check (there: identity-header collapsing in PHP/CGI; here: field resolution via `json_name`/`name` aliasing) is stronger than the producer-side/guard-side name-equality check, so an attacker-controlled alternate spelling of an already-validated key smuggles a second, overriding value past the "single value" invariant.

By contrast, the C++/upb binary path tracks "have I seen this field" on the resolved `Field` handle, not the input string, so equivalent alias collisions are correctly detected there: [4](#0-3) 
This confirms the flaw is specific to the pure-Python `json_format` duplicate-detection logic, not an inherent limitation of ProtoJSON parsing.

### Impact Explanation
Applications that use `google.protobuf.json_format.Parse`/`ParseDict` on ProtoJSON received from untrusted clients, and that rely on Protobuf's documented duplicate-field rejection as a defense (e.g., to detect/refuse conflicting or tampered input, or where an upstream layer inspects the request only for the canonical spelling and assumes protobuf will reject a second, conflicting spelling), can have an attacker silently override an already-set field value (e.g., a role, quantity, or price field) without any parse error being raised. This is a data-integrity/validation-bypass issue analogous to a Medium-severity identity-spoofing class: an attacker-supplied alternate name for a legitimate field escapes the "no duplicates" invariant and the last-processed alias wins.

### Likelihood Explanation
This requires: (1) the target message have at least one field whose `json_name` differs from its declared `name` (the common case, since default `json_name` is lowerCamelCase whenever the field name contains an underscore), and (2) the pure-Python `json_format` parser be used on attacker-controlled ProtoJSON (the default backend in many deployments, e.g. `google.protobuf` without the C++ implementation, or any explicit `json_format.Parse` call). No malicious schema, privileged access, or resource exhaustion is needed — an ordinary two-key JSON object suffices. The bug is already reproduced by the shipped unit tests, making it trivially confirmable.

### Recommendation
Track "seen" state in `_ConvertFieldValuePair` by the resolved `FieldDescriptor` (and its `containing_oneof`), not by the raw JSON key string, mirroring the approach already used by the C++/upb JSON parser (`Traits::RecordAsSeen(*field, msg)`), so that any two JSON keys resolving to the same field trigger the existing "should not have multiple X fields" `ParseError` regardless of which alias/spelling was used.

### Proof of Concept
Using the existing test message `proto3.TestMessage` (field `int32_value`, `json_name` `int32Value`):
```python
from google.protobuf import json_format
from google.protobuf.internal import json_format_proto3_pb2

msg = json_format_proto3_pb2.TestMessage()
json_format.Parse('{"int32Value": 1, "int32_value": 2}', msg)
print(msg.int32_value)  # -> 2, no ParseError raised despite both keys naming the same field
```
This is the exact scenario already codified as `testDuplicateFieldAlternateNames` in the repository's own test suite: [5](#0-4) 
confirming the duplicate-field guard is bypassed by alternate spelling and the second value silently wins, with no exception thrown — unlike the identical-key case which correctly raises `Failed to load JSON: duplicate key int32Value.`: [6](#0-5)

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

**File:** python/google/protobuf/internal/json_format_test.py (L1197-1215)
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
