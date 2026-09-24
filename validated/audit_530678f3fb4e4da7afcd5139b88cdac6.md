Note on investigation: I was cut off before reading the exact `names`-set duplicate-check implementation in `python/google/protobuf/json_format.py` line-by-line (grep found two matches but I could not view the surrounding lines). The conclusion below rests on the confirmed test evidence at `python/google/protobuf/internal/json_format_test.py:1197-1205` [1](#0-0)  and the related conformance/Java test evidence, which together establish the behavior with high confidence even though I could not personally step through the `json_format.py` check function.

### Title
ProtoJSON duplicate-field detection can be bypassed via alternate field-name spelling, causing silent last-writer-wins field smuggling - (File: python/google/protobuf/json_format.py)

### Summary
The Apache CVE's failed invariant is that two logically-equivalent representations of the same identity signal (a header, interpreted once by a hop-by-hop-stripping proxy and again by the origin) are checked inconsistently, letting an attacker smuggle a value past an IP-based authorization check. The Protobuf analog is Python's ProtoJSON parser (`json_format.Parse`), whose duplicate-key rejection is meant to make "a field was assigned twice" an error, but the rejection only compares the literal JSON key string, not the resolved field. Two different literal spellings of the same field (`int32Value` vs `int32_value`) both resolve to the same underlying field, but the duplicate check does not catch this, silently applying "last one wins" instead of rejecting the input.

### Finding Description
`json_format.Parse` is documented and tested to reject JSON objects containing the same key twice (`testDuplicateField`, `CheckError('{"int32Value": 1,\n"int32Value":2}', 'Failed to load JSON: duplicate key int32Value.')`). This duplicate-rejection is the invariant a consuming application may rely on to assume a JSON payload assigns each field at most once.

However, `testDuplicateFieldAlternateNames` in `python/google/protobuf/internal/json_format_test.py:1197-1205` demonstrates that this check operates on the raw JSON key text, not on the field it resolves to: [1](#0-0)  When the same field is specified twice using two different but equally valid spellings — the protobuf `json_name` (`int32Value`) and the underlying field's snake_case name (`int32_value`) — the parser does not detect the duplicate and silently keeps the second value (`int32_value=2` wins over the first `int32Value=1`), instead of raising `InvalidProtocolBufferException`/`ParseError` as it would for an exact-text duplicate.

The equivalent Java implementation shows this ambiguity is treated inconsistently across languages/formats too: Java's `JsonFormat` explicitly rejects `optionalNestedMessage`/`optional_nested_message` duplicates as an error (`testParserRejectDuplicatedFields`, `java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java:910-929`) [2](#0-1) , while Python's pure implementation does not for the same class of alternate-name duplicate. The C++/upb-backed `json/internal/parser.cc` duplicate-key tracking (`RecordAsSeen`) is also documented to only fully enforce this outside of "legacy nonconformant" mode [3](#0-2) , showing the same invariant ("one JSON payload, one unambiguous value per field") is enforced differently by parser mode/language, exactly mirroring the CVE's core defect: two logically equivalent representations of the same target (a field, or a header) are matched by literal identity in one code path and by canonical/semantic identity in another, and an attacker can exploit the mismatch to smuggle a value that the literal-match check does not see as a duplicate.

### Impact Explanation
An application that treats "protobuf will reject duplicate/conflicting JSON keys" as an integrity guarantee — for example, rejecting a payload if a security-relevant field (role, scope, tenant ID) is specified more than once as a way to detect tampering or concatenated/merged payloads — can have that guarantee silently defeated. An attacker submits the field twice using two different valid spellings; the parser accepts the payload (no error), and the second occurrence silently overwrites the first, with no signal to the caller that an ambiguous/duplicate assignment occurred. This is a data-integrity/validation-bypass analog to the CVE's authorization bypass: the attacker controls which of two conflicting values "wins" without triggering the check meant to catch that condition.

### Likelihood Explanation
Reachable through the fully public API `json_format.Parse` with a bounded, well-formed JSON payload and a standard/trusted schema — no privileged access or hostile schema required. However, exploitation requires that the consuming application actually depends on duplicate-key rejection as a security control, which is not a documented protobuf security guarantee (the maintainers' own test comment calls this "non-spec... an oversight bug... would be a breaking change to fix," i.e., a known, accepted, low-priority defect). This substantially reduces both likelihood of a real security impact and appropriate severity, versus the Critical, unconditionally-exploitable Apache bug it's analogized from.

### Recommendation
If duplicate-field detection is to be treated as a validation feature, `json_format.py`'s duplicate-key set should be keyed by the resolved `FieldDescriptor` (as Java does) rather than by the literal JSON key text, so that `int32Value` and `int32_value` (and any other alternate JSON-name spelling of the same field) are recognized as the same field for duplicate purposes across all three C++/Java/Python (and upb) implementations. At minimum, document explicitly that ProtoJSON duplicate-key rejection is best-effort and must not be relied upon for security-relevant validation.

### Proof of Concept
Using the existing repository test as the reproduction (no new code needed, already present and passing as documented behavior, not a crash):
```python
# python/google/protobuf/internal/json_format_test.py:1197-1205
parsed_message = json_format_proto3_pb2.TestMessage()
json_format.Parse('{"int32Value": 1,"int32_value":2}', parsed_message)
assert parsed_message.int32_value == 2  # silently overwritten, no duplicate-key error raised
```
This demonstrates the exact bypass: an exact-text duplicate (`testDuplicateField`) is rejected, but the semantically-identical duplicate via alternate spelling is silently accepted with last-value-wins semantics, exactly as documented in the test file. [4](#0-3)

### Citations

**File:** python/google/protobuf/internal/json_format_test.py (L1191-1205)
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
```

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L917-929)
```java
    // Duplicated optional fields.
    try {
      TestAllTypes.Builder builder = TestAllTypes.newBuilder();
      mergeFromJson(
          "{\n"
              + "  \"optionalNestedMessage\": {},\n"
              + "  \"optional_nested_message\": {}\n"
              + "}",
          builder);
      assertWithMessage("expected exception").fail();
    } catch (InvalidProtocolBufferException e) {
      // Exception expected.
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
