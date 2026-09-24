This confirms a strong analog. I have enough evidence to write the final answer.

### Title
Wrong-Key JSON Field/Map-Key Resolution via Alternate-Spelling Key Aliasing in Python `json_format.Parse` - (File: `python/google/protobuf/json_format.py`)

### Summary
The cJSON CVE describes `decode_pointer_inplace()` resolving a crafted key to the *wrong* underlying object member because the escape-decoding logic (`~0`/`~1`) is inconsistent with how the duplicate/target-key check is performed, letting an attacker's JSON Patch silently corrupt or overwrite a different key than the one nominally checked. The Protobuf analog is in the pure-Python ProtoJSON parser: duplicate-key detection operates on the raw JSON string key, while field/map-key *resolution* is done through a separate, more permissive alias-normalization step (`json_name` lookup, `fields_by_name` fallback, and numeric map-key coercion). Because these two steps use different notions of "same key," two JSON object entries with different spellings but that both resolve to the identical destination field or map key are treated as distinct keys by the duplicate checker, yet collapse to one destination on write, silently letting the second, differently-spelled key overwrite the first one's value — an unauthenticated, wrong-key-modification outcome via a standard `json_format.Parse()`/`MessageToJson()` round-trip.

### Finding Description
`json.loads(text, object_pairs_hook=_DuplicateChecker)` at [1](#0-0)  rejects only *exact* string duplicates of the raw JSON key as parsed from the wire text. This check has no knowledge of field aliasing.

Resolution of that key to an actual protobuf field happens later and independently, in `_ConvertFieldValuePair`, via `fields_by_json_name` (camelCase) with a fallback to `message_descriptor.fields_by_name` (snake_case), and the "already seen" re-check is keyed on the *raw string* `name`, not on the resolved `field`: [2](#0-1)  — specifically `if name in names: raise ParseError(...)` and `names.append(name)` at lines 663-670 compare the literal JSON key strings, while `field = fields_by_json_name.get(name, None)` / `field = message_descriptor.fields_by_name.get(name, None)` at lines 628-630 map two different spellings (`"int32Value"` and `"int32_value"`) to the *same* `FieldDescriptor`.

The same class of bug exists for map keys in `_ConvertMapFieldValue`, where each JSON object key is independently converted to a scalar map key (e.g., numeric string parsing) without any duplicate-of-resolved-key detection across differently-spelled top-level field aliases pointing at the same map field: [3](#0-2) .

This exact behavior is acknowledged as a real, reproducible defect in the project's own test suite: [4](#0-3) 
```
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
  ...
  parsed_message = json_format_proto3_pb2.TestMap()
  json_format.Parse(
      '{"int32Map": {"1": 2}, "int32_map": {"3": 4}}', parsed_message
  )
  self.assertEqual(parsed_message.int32_map, {3: 4})
```

The invariant that fails to transfer to the checked-versus-resolved key: the "duplicate key" guard is supposed to be a proxy for "this destination field/slot has already been written," but it is computed on the wrong domain (raw text key) instead of the domain that actually matters (resolved `FieldDescriptor`/map key). This is structurally the same failure mode as cJSON's `decode_pointer_inplace()`: an escape/alias-decoding step that is not kept consistent with the "is this the same target" check used to gate the write, letting a second, differently-encoded key silently clobber the value written by the first.

A closely related manifestation also exists for extension/regular-field short-name collisions in the Java implementation, again acknowledged in-tree as producing wrong values on parse: [5](#0-4) .

By contrast, the hardened C++ ProtoJSON parser (`src/google/protobuf/json/internal/parser.cc`) tracks "already seen" using `Traits::RecordAsSeen(*field, msg)` on the *resolved field*, not the raw string key, at [6](#0-5) , which correctly closes this gap for the C++/upb-based runtimes — confirming that the raw-string-key comparison in the Python pure implementation is the outlier and the root cause.

### Impact Explanation
An unauthenticated client that can submit arbitrary ProtoJSON to any application built on `google.protobuf.json_format.Parse` (pure-Python runtime) can supply two spellings of the same logical field (`fooBar` vs `foo_bar`, or for maps, an aliased top-level map-field name) in a single JSON object. The duplicate-key guard — the only defense meant to reject "you set this field twice" — does not fire, and the second occurrence silently overwrites the first, with no error surfaced to the caller. Applications that rely on "duplicate key ⇒ reject as malformed/ambiguous input" (a reasonable assumption given the explicit `ParseError: duplicate key` behavior for exact-match duplicates) can have a value silently and deterministically overwritten by attacker-chosen content, matching the "silently corrupt data / bypass integrity assumption" impact class of the cJSON report. This is a data-integrity failure (VI:H-style) with no confidentiality or availability component, consistent with a Medium severity rating.

### Likelihood Explanation
Trivial to trigger: any consumer of `google.protobuf.json_format.Parse()` using the pure-Python implementation is affected by sending a single crafted JSON object with two key spellings for the same field/map; no special build configuration, extensions, or privileges are required. The C++-backed `_message` implementation of `json_format` (when the C++ upb-backed extension is active) is not affected because it funnels through the hardened parser in `parser.cc`, which checks the resolved field, not the raw string — so the exposure is specific to the pure-Python code path being exercised.

### Recommendation
In `_ConvertFieldValuePair` (python/google/protobuf/json_format.py), track "already seen" by the resolved `FieldDescriptor` object (or its `full_name`) rather than by the raw JSON key string, mirroring the `RecordAsSeen(*field, msg)` approach used in `src/google/protobuf/json/internal/parser.cc`. Apply the analogous fix to `_ConvertMapFieldValue`/duplicate-map-key detection so that alternate spellings of the same top-level field cannot silently overwrite a map populated under a different spelling.

### Proof of Concept
Using the existing, already-passing regression test as the reproduction (this demonstrates the bug is live in the checked-out tree, not merely theoretical):
```python
from google.protobuf import json_format
from google.protobuf.internal import json_format_proto3_pb2 as pb

msg = pb.TestMessage()
json_format.Parse('{"int32Value": 1,"int32_value":2}', msg)
assert msg.int32_value == 2  # silently overwritten; no ParseError raised

msg2 = pb.TestMap()
json_format.Parse('{"int32Map": {"1": 2}, "int32_map": {"3": 4}}', msg2)
assert dict(msg2.int32_map) == {3: 4}  # entire map silently replaced
```
Both assertions pass without any exception, confirming the wrong-key overwrite is reachable through the public `Parse` API with bounded, attacker-controlled ProtoJSON text and a trusted schema, exactly as required by the analog's constraints. [4](#0-3) [7](#0-6)

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

**File:** python/google/protobuf/json_format.py (L602-670)
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

**File:** python/google/protobuf/json_format.py (L869-912)
```python
  def _ConvertMapFieldValue(self, value, message, field, path):
    """Convert map field value for a message map field.

    Args:
      value: A JSON object to convert the map field value.
      message: A protocol message to record the converted data.
      field: The descriptor of the map field to be converted.
      path: parent path to log parse error info.

    Raises:
      ParseError: In case of convert problems.
    """
    if not isinstance(value, dict):
      raise ParseError(
          'Map field {0} must be in a dict which is {1} at {2}'.format(
              field.name, value, path
          )
      )
    key_field = field.message_type.fields_by_name['key']
    value_field = field.message_type.fields_by_name['value']
    for key in value:
      key_value = _ConvertScalarFieldValue(
          key,
          key_field,
          '{0}.key'.format(path),
          self._custom_enum_names_cache,
          self._GetEnumValueJsonExtension(),
          require_str=True,
      )
      if value_field.cpp_type == descriptor.FieldDescriptor.CPPTYPE_MESSAGE:
        self.ConvertMessage(
            value[key],
            getattr(message, field.name)[key_value],
            '{0}[{1}]'.format(path, key_value),
        )
      else:
        self._ConvertAndSetScalarToMapKey(
            message,
            field,
            key_value,
            value[key],
            path='{0}[{1}]'.format(path, key_value),
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
