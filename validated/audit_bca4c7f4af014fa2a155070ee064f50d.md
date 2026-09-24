### Title
JSON alternate-name duplicate-field bypass silently overrides previously parsed value in `json_format.Parse` — (File: `python/google/protobuf/json_format.py`)

### Summary
Python's pure-Python `json_format.Parse()` implements a duplicate-key check that rejects a JSON object containing the exact same key string twice, but the check operates on the raw JSON key string, not on the resolved `FieldDescriptor`. Because a single proto field can legally be addressed in JSON by multiple distinct string spellings (the default lowerCamelCase name, the original proto field name, and/or a custom `json_name`), an attacker can supply two different spellings of the same field in one JSON document. The duplicate-key guard does not fire, and the second occurrence silently overwrites the value set by the first — exactly the "ambiguous representation not normalized before an identity/uniqueness check" pattern described in the CVE (a single logical attribute presented in two syntactic forms is not recognized as the same attribute by a security-relevant equality/uniqueness check).

### Finding Description
The Node.js CVE's failed invariant is: a single logical RDN attribute can be represented in more than one syntactic form (single-value vs. multi-value RDN), and code that treats these two encodings as distinct — instead of normalizing before doing identity/consistency checks — can be tricked into accepting an attacker-chosen value under one representation while the real check happened against a different representation.

The analogous failed invariant in this codebase is in Python's own JSON parsing path: `python/google/protobuf/json_format.py`'s duplicate-field detector, and its behavior is explicitly documented as broken in the test suite: [1](#0-0) 

```
def testDuplicateField(self):
    self.CheckError(
        '{"int32Value": 1,\n"int32Value":2}',
        'Failed to load JSON: duplicate key int32Value.',
    )

def testDuplicateFieldAlternateNames(self):
    # Note: this behavior is non-spec and an oversight bug in the
    # implementation, but would be a breaking change to fix. The duplicate field
    # checker intends [to] reject inputs with duplicate key names, but it only
    # catches keys that are exact matches and not alternate spellings that
    # correspond to the same field.
    parsed_message = json_format_proto3_pb2.TestMessage()
    json_format.Parse('{"int32Value": 1,"int32_value":2}', parsed_message)
    self.assertEqual(parsed_message.int32_value, 2)
```

The same ambiguity exists for map fields (`testDuplicateFieldAlternateNamesMap`), where `{"int32Map": {"1": 2}, "int32_map": {"3": 4}}` results in only the second spelling's map surviving, with no error.

This is a project-acknowledged design hazard, not merely a test artifact — `docs/design/editions/edition-zero-json-handling.md` documents that JSON field-name conflicts are ambiguous by construction and that resulting parse behavior is "deterministic in all of the cases we've encountered, [but] inconsistent across runtimes and unexpected": [2](#0-1) 

Contrast this with the C++/upb JSON parser (`src/google/protobuf/json/internal/parser.cc`), which records "seen" state per resolved field object (`Traits::RecordAsSeen(*field, msg)`), after name resolution — so the C++ implementation is not vulnerable to this particular alternate-spelling bypass: [3](#0-2) 

The Python pure-Python path's duplicate check therefore fails to normalize the attacker-controlled key text to the underlying `FieldDescriptor` identity before performing its "already seen" comparison — the same class of failure as accepting a multi-value RDN as if it were an unambiguous single value: two different attacker-supplied encodings of the same logical entity are not unified before an important identity/uniqueness decision is made.

### Impact Explanation
Any application that (a) uses `google.protobuf.json_format.Parse` on the pure-Python implementation to accept untrusted JSON, and (b) relies on protobuf's documented duplicate-key rejection as an integrity guard (e.g., "reject payloads that try to set a field twice," or code/log auditing that only checks for literal duplicate JSON keys before feeding the payload to the parser) can have a field silently overwritten by a second, differently-spelled key that evades the duplicate check. This is an integrity/validation-bypass issue: attacker-controlled data ends up in a field the application believed was authoritatively set once, mirroring the CVE's downstream impact of a spoofed identity attribute bypassing verification logic built on the assumption of a single canonical representation.

The impact is bounded — it requires the application to build a security decision on protobuf's JSON duplicate-key detection or on assuming one JSON key string == one field with no possible aliasing, which is a narrower exposure than a memory-safety or RCE issue, but it is a real, provable behavioral confusion in a supported public parsing API (`json_format.Parse`).

### Likelihood Explanation
High likelihood of the mechanism being triggerable: the bypass requires only a bounded, well-formed JSON document with two key spellings of the same field (no schema tricks, no privileged access), sent through the standard public `Parse` API. It is fully reproducible and already codified as a passing (not merely theoretical) test case in the repository. The likelihood of real-world security impact depends on whether a specific consuming application relies on the duplicate-key guarantee, which cannot be verified generically — this is stated as an assumption per the report's consuming-application-exposure rule.

### Recommendation
- In `python/google/protobuf/json_format.py`, perform duplicate-field detection after resolving the JSON key to its `FieldDescriptor` (as the C++/upb parser does), not on the raw string key, so that `int32Value` and `int32_value` (or any custom `json_name`) are recognized as the same field for uniqueness purposes.
- Document clearly (and enforce, where feasible) that `Parse` should fail closed on any alternate-spelling collision, consistent with the guarantee callers already believe exists (as evidenced by the existing `testDuplicateField` test).
- Track this together with the broader Edition Zero JSON field-name-conflict unification effort referenced in `docs/design/editions/edition-zero-json-handling.md`, since the underlying ambiguity (multiple JSON key forms mapping to one field) is the root cause shared by both the compiler-time conflict detection and this runtime bypass.

### Proof of Concept
Using the existing, currently-passing test in `python/google/protobuf/internal/json_format_test.py` as the reproduction:

```python
from google.protobuf import json_format
from google.protobuf.internal import json_format_proto3_pb2

msg = json_format_proto3_pb2.TestMessage()
# Two different JSON spellings of the same field, int32_value:
json_format.Parse('{"int32Value": 1,"int32_value":2}', msg)
assert msg.int32_value == 2   # second, differently-spelled key silently wins
# No "duplicate key" error is raised, even though json_format.Parse
# explicitly raises one for `{"int32Value": 1,"int32Value":2}`.
```

This demonstrates that the duplicate-key protection is spelling-sensitive rather than field-identity-sensitive — an attacker-controlled second value for the same logical field silently overrides the first without any error, exactly analogous to how an ambiguous multi-form encoding could defeat a naive identity/uniqueness check in the CVE. [4](#0-3)

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

**File:** docs/design/editions/edition-zero-json-handling.md (L26-31)
```markdown
*   All proto messages can be serialized to JSON
    *   Conflicting mappings will produce JSON with duplicate keys
*   All proto messages can be parsed from JSON
    *   Conflicting mappings lead to undefined behavior. While the behavior is
        deterministic in all of the cases we've encountered, it's inconsistent
        across runtimes and unexpected.
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
