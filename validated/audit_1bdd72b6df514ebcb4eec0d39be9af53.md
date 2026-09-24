## Finding: JSON parser duplicate-field detection is bypassed by alternate spellings of the same field name

### External bug class mapped to Protobuf

CVE-2023-27043 stems from Python's `email` module failing to canonicalize an address before applying a security check: two textually different representations of the *same* address field are treated inconsistently, so a validator that expects rejection/consistent handling of a header value can be fooled by an alternate-but-equivalent spelling. The failed invariant is: *"a single logical field, addressable by more than one textual spelling, must be evaluated consistently by whatever de-duplication/validation logic guards it."*

The closest Protobuf analog is in the pure-Python JSON parser's duplicate-key rejection logic.

### Title
Duplicate-field/oneof-collision detection in ProtoJSON parsing is bypassed via alternate field-name spellings (camelCase vs. proto/snake_case name) - (File: `python/google/protobuf/json_format.py`)

### Summary
`json_format.Parse` (the public ProtoJSON parsing entry point in the pure-Python backend) is supposed to reject a JSON object that assigns the same protobuf field twice, since a repeated key is ambiguous and its two occurrences may not agree. The rejection is implemented by comparing the *raw* JSON key strings seen so far [1](#0-0) , not the *resolved* `FieldDescriptor`. Because a field can be legally addressed by either its `json_name` (e.g. `int32Value`) or its proto `name` (e.g. `int32_value`), both spellings resolve to the same field object via `fields_by_json_name`/`fields_by_name` lookup [2](#0-1) , yet the string-identity check in `names` never treats them as a collision, so the second spelling silently overwrites the first with no error raised.

### Finding Description
`_ConvertFieldValuePair` builds a lookup table `fields_by_json_name` and, for every JSON key, resolves it to a `FieldDescriptor` first via `json_name`, then falls back to the proto field name, then to extension syntax [3](#0-2) . Once a field is resolved, the code performs its "duplicate field" defense by appending the *raw key string* to a `names` list and checking membership before processing each key [1](#0-0) :
```
if name in names:
  raise ParseError(...'should not have multiple ... fields'...)
names.append(name)
```
This check is keyed on the untranslated JSON text (`"int32Value"` vs `"int32_value"`), not on the field identity that was already computed one line above. An attacker-controlled payload containing both spellings of the same field passes the "already seen" check twice (as two distinct strings) even though both map to the identical underlying field, so the second occurrence's value simply overwrites the first via `setattr`/`ClearField` further down in the same function, with **no exception raised**.

This is explicitly acknowledged as a known, unfixed oversight in the project's own tests: `testDuplicateFieldAlternateNames` and `testDuplicateFieldAlternateNamesMap` [4](#0-3)  demonstrate that `{"int32Value": 1, "int32_value": 2}` parses successfully to `int32_value == 2` instead of raising "duplicate field" error, and that the same holds for map fields keyed by alternate map-field spellings (`int32Map` vs `int32_map`).

The upstream `.proto` schema-level defense, `CheckFieldJsonNameUniqueness` [5](#0-4) , only detects *schema authors* declaring two fields with colliding JSON names — it operates on trusted `.proto` definitions at descriptor-build time, and does nothing to prevent an attacker's *JSON payload* from addressing one legitimate field through two of its own legal alternate spellings at parse time. There is no equivalent runtime canonicalization check protecting the parse path itself.

### Impact Explanation
The invariant the "duplicate field" check is meant to enforce is that a JSON payload must have exactly one unambiguous value per logical field — this is precisely the guarantee applications rely on when they perform pre-parse validation/allow-listing of raw JSON keys (e.g., an API gateway, WAF, or business-logic layer that inspects the textual JSON for a specific key such as `"int32Value"` to authorize or audit a value) before handing the same bytes to `json_format.Parse`. Because the duplicate check silently no-ops on alternate spellings, an attacker can smuggle a second, different value for the same field under its alternate name (`int32_value`) that a naive text-based validator upstream never inspected, while the value the application actually consumes downstream (the parsed proto field) is the attacker's second value — a classic "which name did you validate vs. which name won" confusion, structurally identical to the addr-spec confusion in CVE-2023-27043. The same bypass applies to map fields (`int32Map` vs `int32_map`), silently dropping/overwriting the first map's entries.

Because this only affects data-integrity/consistency (a value the application should have rejected as ambiguous silently "wins" instead), and does not itself cause memory corruption, this is Medium at most (matching the source CVE's rated severity), and only relevant if a consuming application performs some kind of raw-JSON pre-validation gated on field name spelling.

### Likelihood Explanation
High reachability: this is exercised purely by calling the public `google.protobuf.json_format.Parse` API with attacker-supplied JSON text; no privileged access, custom schema, or native code path is required. It is reproducible with any message containing fields whose default `json_name` differs from the proto field `name` (true for essentially every multi-word field), or any `map<>` field. It is already reproduced and pinned by the project's own test suite [4](#0-3) , but is deliberately left unfixed as "would be a breaking change."

### Recommendation
In `_ConvertFieldValuePair` (and the map-field/oneof duplicate-tracking equivalent), key the "already seen" set by the resolved `FieldDescriptor` (or its `full_name`) rather than by the raw JSON key text, so that any two spellings resolving to the same field are correctly detected as a duplicate and rejected (matching the intent already coded at [1](#0-0) ). Apply the analogous fix to `_ConvertMapFieldValue`'s key handling for maps whose field itself has alternate spellings.

### Proof of Concept
Using the existing checkout's test utilities (`python/google/protobuf/internal/json_format_test.py`):
```python
from google.protobuf import json_format
from google.protobuf.internal import json_format_proto3_pb2 as pb2

msg = pb2.TestMessage()
# Same field addressed by two legal spellings: json_name vs proto name.
json_format.Parse('{"int32Value": 1, "int32_value": 2}', msg)
assert msg.int32_value == 2   # No ParseError raised, silent overwrite instead of rejection.
```
This reproduces `testDuplicateFieldAlternateNames` [6](#0-5)  and the map-field variant [7](#0-6) : both confirm no exception is raised despite the intended duplicate-key rejection logic in `_ConvertFieldValuePair` [1](#0-0) .

### Citations

**File:** python/google/protobuf/json_format.py (L602-648)
```python
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

**File:** src/google/protobuf/descriptor.cc (L6230-6247)
```text
void internal::DescriptorBuilder::CheckFieldJsonNameUniqueness(
    const absl::string_view message_name, const DescriptorProto& message,
    const Descriptor* descriptor, bool use_custom_names) {
  absl::flat_hash_map<std::string, JsonNameDetails> name_to_field;
  for (const FieldDescriptorProto& field : message.field()) {
    JsonNameDetails details = GetJsonNameDetails(&field, use_custom_names);
    if (details.is_custom && JsonNameLooksLikeExtension(details.orig_name)) {
      auto make_error = [&] {
        return absl::StrFormat(
            "The custom JSON name of field \"%s\" (\"%s\") is invalid: "
            "JSON names may not start with '[' and end with ']'.",
            field.name(), details.orig_name);
      };
      AddError(message_name, field, DescriptorPool::ErrorCollector::NAME,
               make_error);
      continue;
    }
    auto it_inserted = name_to_field.try_emplace(details.orig_name, details);
```
