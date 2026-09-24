### Title
Duplicate-key detection in Python `json_format.Parse` uses exact string matching and fails to catch alternate spellings (`camelCase` vs `snake_case`) that alias the same field, allowing silent value/entry overwrite - ([File: python/google/protobuf/json_format.py])

### Summary
The GMX report's failed invariant is: an equality check (`longToken == shortToken`) is used as a proxy for "these two identifiers refer to distinct underlying storage," but the check is defeated by tokens that have two different addresses sharing the same storage, causing double counting / incorrect aggregation. The transferable Protobuf analog is Python's `json_format.Parse` duplicate-field/duplicate-map-key checker, which is documented in-repo as intentionally rejecting duplicate JSON keys, but which "only catches keys that are exact matches and not alternate spellings that correspond to the same field" (e.g. `int32Value` vs `int32_value`, or `int32Map` vs `int32_map`). Both bugs share the same root cause: an identity/uniqueness check performed on a surface-level string/address key instead of on the resolved underlying target, letting distinct-looking-but-aliased inputs bypass a duplicate/uniqueness guard and silently mutate state that the caller assumed was validated as unique.

### Finding Description
`google.protobuf.json_format.Parse` walks a JSON object's keys and, per the implementation's own test comments, is *supposed* to reject a payload containing the same logical field twice. In practice the duplicate check compares the literal JSON key strings pulled from the input against previously-seen literal key strings, not against the resolved `FieldDescriptor` that the key maps to. Because Protobuf JSON allows a field to be addressed by either its `json_name` (default lowerCamelCase) or its original `name` (snake_case) - both accepted on parse per the spec - two textually different keys can resolve to the *same* field or the same map. This is directly analogous to two token contract addresses resolving to the same underlying storage in the GMX bug: the "same object" test (`longToken == shortToken` there; exact string equality here) is the wrong invariant, because the real identity that matters (the resolved field/map, or the resolved token storage) can be reached through more than one surface representation.

The maintainers explicitly acknowledge this in the test file itself: [1](#0-0) 

which documents that `{"int32Value": 1, "int32_value": 2}` is silently accepted (last-wins, second value replaces first) even though the duplicate-key checker is intended to reject exactly this pattern - it only compares literal strings, and `"int32Value" != "int32_value"` even though both alias `int32_value`. The same class of bug repeats for scalar map fields: `{"int32Map": {"1": 2}, "int32_map": {"3": 4}}` silently merges into a map containing only `{3: 4}`, discarding the first entry without any duplicate-key error, again because `"int32Map"` and `"int32_map"` are treated as unrelated keys even though they alias the same map field.

By contrast, the C++/upb-based JSON parser path does perform this check correctly at the resolved-field level (via `Traits::RecordAsSeen(*field, msg)` after `FieldByName`/JSON-name resolution), and rejects true duplicates including cross-spelling collisions when they resolve to the same `FieldDescriptor`: [2](#0-1) 
This confirms the invariant *can* be enforced correctly (field-identity-based), and that the pure-Python implementation's approach (string-identity-based) is the outlier that fails to enforce it — mirroring how `getPoolDivisor`'s address-identity check is the wrong invariant relative to a storage-identity check.

Also relevant: `MessageDescriptor` (C#) and `getFieldNameMap` (Java) both deliberately build a single map keyed by *both* `Name` and `JsonName` pointing at the same `FieldDescriptor`, explicitly to allow either spelling to resolve to the same field: [3](#0-2) [4](#0-3) 
This is the exact mechanism that creates the "multi-address" aliasing surface (two JSON key spellings -> one field identity) that the report's bug class is about; the C# `JsonParser.Merge` correctly does per-field duplicate tracking after resolution (`jsonFieldMap.TryGetValue(name, out FieldDescriptor field)` then tracking `field`/`ContainingOneof`, not the raw string) while the pure-Python `json_format.py` implementation does not.

### Impact Explanation
This is a data-integrity/silent-corruption bug in the *consuming application's* trust boundary: an attacker-controlled JSON payload sent to any application that calls `google.protobuf.json_format.Parse()` on untrusted input can smuggle two conflicting values for the same field or map key under different spellings. The parser does not raise the "duplicate key" error the application likely relies on for input validation/anti-tampering, and instead silently applies last-wins semantics (for scalar fields) or silently drops entries (for map fields). This is a real integrity violation directly analogous to the GMX pool value being silently corrupted: a caller's assumption ("this API rejects duplicate/ambiguous input") is violated by a bypassable identity check, with a strictly worse-because-silent failure mode. It does not on its own crash, leak memory, or achieve RCE, matching the report's own Medium classification for the GMX finding.

### Likelihood Explanation
Every schema with `json_name` differing from `name` (i.e., any field that isn't literally `snake_case == lowerCamelCase`, which is the vast majority of multi-word field names) is affected, and any application using the Python pure implementation of `json_format.Parse` on attacker-supplied JSON is exposed - no privileged access or special schema required. It is a reachable, unauthenticated-input bug via the public `Parse`/`MessageToJson`-round-trip API. It's already reproduced and documented by protobuf's own test suite (`testDuplicateFieldAlternateNames`, `testDuplicateFieldAlternateNamesMap`), which the maintainers flag as "a non-spec ... oversight bug ... but would be a breaking change to fix" - i.e., a confirmed, currently-unfixed condition in this codebase, not a hypothetical.

### Recommendation
Perform duplicate/uniqueness tracking at the resolved-field level rather than the raw-string level in the pure-Python `Parse` implementation: after resolving a JSON key to a `FieldDescriptor` (via either `json_name` or `name`), record the `FieldDescriptor` object (or its full name) in the "seen" set, and raise the existing `duplicate key` `ParseError` if that descriptor has already been seen in the current object - mirroring the upb/C++ parser's `Traits::RecordAsSeen(*field, msg)` approach and the C# `JsonParser.Merge`'s per-`FieldDescriptor` tracking. Apply the same fix to map-field parsing so that `int32Map`/`int32_map` spelling collisions are rejected rather than silently merged/overwritten.

### Proof of Concept
Using the checkout's own conformance test (already present, demonstrating the bug is real and current): [5](#0-4) 
```python
from google.protobuf import json_format
from google.protobuf.internal import json_format_proto3_pb2

# True duplicate: correctly rejected today.
m = json_format_proto3_pb2.TestMessage()
try:
    json_format.Parse('{"int32Value": 1, "int32Value": 2}', m)
    print("BUG: exact duplicate accepted")
except json_format.ParseError:
    print("OK: exact duplicate rejected as expected")

# Alternate-spelling duplicate: same field, different JSON key spelling -
# accepted silently, no ParseError, last write wins.
m2 = json_format_proto3_pb2.TestMessage()
json_format.Parse('{"int32Value": 1, "int32_value": 2}', m2)
print(m2.int32_value)  # prints 2 -- silently overwritten instead of raising

# Map variant: first entry silently dropped, no error raised.
mp = json_format_proto3_pb2.TestMap()
json_format.Parse('{"int32Map": {"1": 2}, "int32_map": {"3": 4}}', mp)
print(dict(mp.int32_map))  # {3: 4} -- entry for key 1 silently discarded
```
Both non-exception outcomes above confirm that the "duplicate key" invariant the parser advertises (and enforces for exact-string duplicates) is bypassed whenever the attacker supplies the aliasing alternate spelling of the same field/map, exactly matching the report's core failure mode of an identity check being defeated by a surface-representation alias of the same underlying target.

### Citations

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

**File:** csharp/src/Google.Protobuf/Reflection/MessageDescriptor.cs (L94-109)
```csharp
        private static ReadOnlyDictionary<string, FieldDescriptor> CreateJsonFieldMap(IList<FieldDescriptor> fields)
        {
            var map = new Dictionary<string, FieldDescriptor>();
            // The ordering is important here: JsonName takes priority over Name,
            // which means we need to put JsonName values in the map after *all*
            // Name keys have been added. See https://github.com/protocolbuffers/protobuf/issues/11987
            foreach (var field in fields)
            {
                map[field.Name] = field;
            }
            foreach (var field in fields)
            {
                map[field.JsonName] = field;
            }
            return new ReadOnlyDictionary<string, FieldDescriptor>(map);
        }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1827-1842)
```java
    // Maps from camel-case field names to FieldDescriptor.
    private final Map<Descriptor, Map<String, FieldDescriptor>> fieldNameMaps =
        new HashMap<Descriptor, Map<String, FieldDescriptor>>();

    private Map<String, FieldDescriptor> getFieldNameMap(Descriptor descriptor) {
      if (!fieldNameMaps.containsKey(descriptor)) {
        Map<String, FieldDescriptor> fieldNameMap = new HashMap<String, FieldDescriptor>();
        for (FieldDescriptor field : descriptor.getFields()) {
          fieldNameMap.put(field.getName(), field);
          fieldNameMap.put(field.getJsonName(), field);
        }
        fieldNameMaps.put(descriptor, fieldNameMap);
        return fieldNameMap;
      }
      return fieldNameMaps.get(descriptor);
    }
```
