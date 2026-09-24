## Analysis

The Traefik bug's essential shape is: **a security-relevant "no duplicate/aliased name may sneak past this check" invariant is enforced by scanning one representation of a name-space (`req.Header`) while a second, semantically-equivalent representation (`req.Trailer`) that resolves to the same logical entity is never checked, so a differently-spelled/differently-channeled instance of the "same" name defeats the defense.**

The Protobuf analog lives in the pure-Python ProtoJSON parser's field-value pair converter.

### Location
`python/google/protobuf/json_format.py`, `_Parser._ConvertFieldValuePair` [1](#0-0) 

The duplicate-field guard tracks the **raw literal JSON key string** it has already seen:

```python
for name in js:
  field = fields_by_json_name.get(name, None)
  if not field:
    field = message_descriptor.fields_by_name.get(name, None)
  ...
  if name in names:
    raise ParseError('Message type "{0}" should not have multiple "{1}" fields at "{2}".' ...)
  names.append(name)
``` [2](#0-1) 

A field can be reached through **two different literal spellings** — its `json_name` (e.g. `int32Value`) and its declared proto `name` (e.g. `int32_value`) — both resolved via `fields_by_json_name` / `fields_by_name` at lines 604-605, 628-630 [3](#0-2) . The duplicate check compares the **string** `name`, not the **resolved `field` identity**, so `{"int32Value": 1, "int32_value": 2}` never triggers the "should not have multiple fields" error — both keys are processed, and the second silently overwrites the first with no error.

This exactly mirrors Traefik's flaw: `removeAliasingHeaders`/`rejectAliasingHeaders` scanned `req.Header` names only and missed the aliased channel `req.Trailer` that resolves to the same trusted name. Here, `_ConvertFieldValuePair`'s duplicate-name reject scans the literal string channel (`names` list of raw JSON keys) only, and misses that a *different* string resolves to the *same field* — the field-identity channel is never checked for aliasing.

By contrast, the sibling **oneof** duplicate check in the same function is done correctly, by resolved semantic identity (`field.containing_oneof.name`), not literal text [4](#0-3) , confirming that resolved-identity tracking was the intended invariant for the plain per-field case too, but the implementation regressed to string comparison there.

### Confirmed, still-present, known behavior
This exact bypass is documented as a live, un-fixed oversight in the test suite:

`python/google/protobuf/internal/json_format_test.py`:
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
``` [5](#0-4) 

The same gap applies to map fields (`testDuplicateFieldAlternateNamesMap`) [6](#0-5) .

Note the contrasting C++/upb JSON parser (`src/google/protobuf/json/internal/parser.cc`) does this correctly — it computes `SeenState seen = Traits::RecordAsSeen(*field, msg)` keyed by the **resolved field**, not the literal JSON key text [7](#0-6) , so this defect is specific to the pure-Python fallback implementation of `google.protobuf.json_format`.

## Assessment against the report's bug class

- **Attacker-controlled value**: any client of a service calling `json_format.Parse`/`ParseDict`/`MessageToDict`'s inverse on untrusted ProtoJSON — a supported public parse API, bounded payload, trusted schema.
- **Failed invariant**: "a message must not have multiple values supplied for the same logical field" — the documented purpose of the `should not have multiple "%s" fields` error, intended as protection against ambiguous/duplicate-value input (the ProtoJSON analog of duplicate-header/duplicate-trailer confusion).
- **Missing check**: aliasing by literal string instead of by resolved `FieldDescriptor` identity, exactly like Traefik's per-header-name-string scan missing the aliasing trailer channel that resolves to the same trusted name.
- **Consuming-application exposure assumption**: applications that rely on Protobuf's ProtoJSON parser to reject duplicate/conflicting field submissions as an anti-ambiguity or anti-tampering safeguard (e.g., rejecting a payload that supplies a security-relevant field — quantity, price, permission scope, idempotency key — twice) will instead silently accept the conflicting payload with last-write-wins semantics, with no indication a conflict occurred.
- **Impact**: silent value confusion, not memory corruption; this is consistent with a Medium-severity data-integrity/parser-inconsistency finding rather than Critical/High, since no allocation, DoS, or execution primitive is implicated, and the excluded classes (raw-byte gateway differential, harmless depth discrepancy) do not apply here — this is a same-process, single-parse silent bypass of a documented rejection guarantee.

### Title
Duplicate-field rejection in ProtoJSON parsing bypassed via aliased JSON key spelling — (File: `python/google/protobuf/json_format.py`)

### Summary
`_Parser._ConvertFieldValuePair` in the pure-Python ProtoJSON parser detects "multiple fields with the same name" by comparing the literal JSON key string against a list of previously-seen literal strings (`names`), instead of the resolved `FieldDescriptor`. Because a field can be addressed by two distinct strings (its `json_name` and its declared `name`), an attacker can submit both spellings of the same field in one JSON object; the duplicate check never fires, and the second occurrence silently overwrites the first with no error, defeating the parser's own advertised "should not have multiple fields" protection.

### Finding Description
`fields_by_json_name` and `message_descriptor.fields_by_name` both resolve to the same `field` object for camelCase vs. snake_case (or custom `json_name`) spellings [8](#0-7) . The duplicate-detection guard tracks `names.append(name)` / `if name in names` using the raw un-resolved string key, not `field` [9](#0-8) . Consequently `{"int32Value": 1, "int32_value": 2}` parses without error, with the field ending at value `2` — the identical bypass shape as Traefik scanning `req.Header` by name and missing the semantically-equivalent `req.Trailer` alias.

### Impact Explanation
Any application depending on the ProtoJSON parser's duplicate-field rejection as a defense against ambiguous or conflicting attacker-supplied values (a documented, advertised guarantee — `ParseError: should not have multiple "%s" fields`) gets silent overwrite instead. This is a genuine, exploitable violation of a documented invariant reachable through the public parse API with bounded, schema-valid input, but its blast radius is confined to value confusion within a single parse call (no cross-process/differential-parser claim is made here), which bounds it to Medium severity.

### Likelihood Explanation
Trivial to trigger: any two-key JSON object where both keys resolve to the same field via `json_name`/`name` aliasing (which exists for essentially every field in every proto3 message with default JSON name mapping). No special preconditions beyond calling the standard `json_format.Parse`/`ParseDict` API on untrusted JSON, which is the ordinary, supported, unauthenticated-input use case for ProtoJSON.

### Recommendation
Change the duplicate-detection guard in `_ConvertFieldValuePair` to track the resolved `field` object (or `field.full_name`) rather than the raw literal `name`, mirroring the already-correct oneof check at lines 672-682 that keys on `field.containing_oneof.name`. This closes the aliasing gap without weakening legitimate use of `json_name`.

### Proof of Concept
```python
from google.protobuf import json_format
from google.protobuf.internal import json_format_proto3_pb2

msg = json_format_proto3_pb2.TestMessage()
json_format.Parse('{"int32Value": 1, "int32_value": 2}', msg)
assert msg.int32_value == 2  # no ParseError raised despite two values for the same field
```
This reproduces the exact behavior already captured by `testDuplicateFieldAlternateNames` in the repository's own test suite [10](#0-9) , confirming the check-bypass is present and unfixed in this checkout.

### Citations

**File:** python/google/protobuf/json_format.py (L591-670)
```python
  def _ConvertFieldValuePair(self, js, message, path):
    """Convert field value pairs into regular message.

    Args:
      js: A JSON object to convert the field value pairs.
      message: A regular protocol message to record the data.
      path: parent path to log parse error info.

    Raises:
      ParseError: In case of problems converting.
    """
    names = []
    message_descriptor = message.DESCRIPTOR
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
        if not field and _VALID_EXTENSION_NAME.match(name):
          if not message_descriptor.is_extendable:
            raise ParseError(
                'Message type {0} does not have extensions at {1}'.format(
                    message_descriptor.full_name, path
                )
            )
          identifier = name[1:-1]  # strip [] brackets
          # pylint: disable=protected-access
          field = message.Extensions._FindExtensionByName(identifier)
          # pylint: enable=protected-access
          if not field:
            # Try looking for extension by the message type name, dropping the
            # field name following the final . separator in full_name.
            identifier = '.'.join(identifier.split('.')[:-1])
            # pylint: disable=protected-access
            field = message.Extensions._FindExtensionByName(identifier)
            # pylint: enable=protected-access
        if not field:
          if self.ignore_unknown_fields:
            continue
          raise ParseError(
              (
                  'Message type "{0}" has no field named "{1}" at "{2}".\n'
                  ' Available Fields(except extensions): "{3}"'
              ).format(
                  message_descriptor.full_name,
                  name,
                  path,
                  [f.json_name for f in message_descriptor.fields],
              )
          )
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
