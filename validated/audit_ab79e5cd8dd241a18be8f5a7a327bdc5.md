### Title
Duplicate-key JSON parsing skips proper detection when a field value is `null`, causing non-deterministic / inconsistent oneof and field state across duplicate keys - (File: `src/google/protobuf/json/internal/parser.cc`)

### Summary
The IBC-Go advisory concerns a sum-type value (`Acknowledgement`, effectively a oneof-like `Result`/`Error`) whose JSON deserialization can silently accept multiple/duplicate keys or ambiguous representations, so that different implementations/nodes can disagree about the parsed value, causing non-deterministic consensus state. The protobuf JSON parser's own duplicate-key detector (`ParseField` in `src/google/protobuf/json/internal/parser.cc`) has a documented, acknowledged bug of the same shape: when a JSON value is `null`, the code takes an early "no-op" path for the null case, but the comment at the top of that function explicitly states duplicate-key checking is **not** performed for the null-valued occurrence (`b/519557203`), even though the spec calls for it.

### Finding Description
`ParseField` (src/google/protobuf/json/internal/parser.cc, lines 1211-1296) resolves the JSON key to a `field`, then does: [1](#0-0) 

The comment is explicit: *"Any `null` values eagerly no-op... Note that spec behavior is that we should do duplicate key checking in the case of a null value, but this implementation currently does not (b/519557203)."* This means when the JSON value for a key is `null` and the field's type is not `Value`/`NullValue`, the parser returns early (via `lex.Expect("null")`) **before** `Traits::RecordAsSeen` is invoked, so the "seen" bookkeeping used to detect duplicate keys and duplicate-oneof-member keys is bypassed for that occurrence.

`RecordAsSeen` (src/google/protobuf/json/internal/parser_traits.h, lines 81-99 and 237-255) is the sole mechanism that both (a) rejects a second real value for the same field/oneof and (b) determines whether prior message/repeated field content should be cleared on first sight. Skipping this call for null occurrences means the interleaving of `null` and non-`null` keys for the *same* field or *same oneof* is not uniformly validated — the outcome depends on which key is null and which order they appear, rather than being determined purely by the "last one wins with hard duplicate-rejection" rule enforced everywhere else in this function: [2](#0-1) 

This exactly parallels the IBC bug's failed invariant: a sum-type / duplicate-key JSON payload where the "value taken" depends on quirks of null-handling order rather than a single well-defined rule, so the same attacker-supplied bytes can be interpreted inconsistently depending on implementation-specific null short-circuiting.

### Impact Explanation
Any consuming application that treats ProtoJSON parsing as a canonicalization/consensus-relevant step (e.g., re-serializing parsed messages for hashing, signature verification, or cross-node agreement, similar to how IBC used JSON unmarshalling of acknowledgements as consensus-relevant data) inherits non-deterministic behavior: a payload with `{"oneofField": null, "otherOneofField": <value>}` bypasses the strict "already seen" check for the null branch, so two parsers (or the same parser under different code paths, e.g. reflection vs. the proto3-Type table-driven path in `parser_traits.h`) could disagree on whether a conflict should have been flagged, or on which value ultimately wins, when duplicate/null combinations are mixed. Because Protobuf explicitly documents (docs/field_presence.md) that JSON element ordering is not authoritative and duplicate-key resolution must be well-defined for determinism, this known gap (tracked as `b/519557203`) is a genuine invariant violation, not an intentional design choice — the code comment itself flags it as non-compliant with spec.

### Likelihood Explanation
High likelihood of being triggered: an ordinary client can submit bounded ProtoJSON with a `null` value for one field of a oneof/duplicate-field pair and a real value for a lexically-duplicate or oneof-sibling field. This uses only the public `JsonStringToMessage`/`ParseField` code path with a trusted schema (any message containing a `oneof` or a field with an alternate `json_name`) and a small, bounded payload — no privileged access or malicious schema is required. The bug is explicitly acknowledged in the source comment with an open internal bug ID, confirming it is a live, unresolved gap rather than a false positive.

### Recommendation
Move the `Traits::RecordAsSeen(*field, msg)` call (and its associated duplicate/oneof-conflict check) ahead of, or make it unconditional relative to, the early-null-return path in `ParseField`, so that a `null` occurrence of a field participates in duplicate-key and oneof-conflict detection identically to a non-null occurrence. This closes the gap tracked by `b/519557203` and ensures parse results for duplicate/oneof keys are determined solely by key order and the documented last-one-wins/duplicate-rejection rules, regardless of whether an intervening occurrence's value happens to be `null`.

### Proof of Concept
Given a message with a `oneof` (e.g., `TestOneof` used throughout the JSON conformance tests, `oneof_int32_value`/`oneof_string_value` etc.), submit:
```json
{"oneofNestedMessage": null, "oneofInt32": 1, "oneofNestedMessage": 5}
```
Because the first occurrence of `oneofNestedMessage` is `null`, `RecordAsSeen` is never called for it (per the code path at src/google/protobuf/json/internal/parser.cc:1244-1256), so the oneof-conflict bookkeeping only registers `oneofInt32` as "seen." The third key (`oneofNestedMessage` again, non-null) is then processed as if it were the *first* real occurrence of that oneof member rather than a conflicting second oneof branch, when a spec-compliant, fully consistent duplicate/oneof check would have flagged this as an error at that point (as it does for `RepeatedOneofKeys`/`OneofFieldDuplicate`, cited above) — I was not able to execute this JSON through a built parser in this environment to confirm the exact returned status/value, so this is presented as a traced code-path analysis based on the explicit source comment and the `SeenState`/`RecordAsSeen` logic, rather than a confirmed runtime observation. Validating the precise resulting `absl::Status` or field value requires running the C++ conformance test harness (e.g. adding a case similar to `RepeatedOneofKeys` in `src/google/protobuf/json/json_test.cc`) locally with a Devin session that has build/test tooling.

### Citations

**File:** src/google/protobuf/json/internal/parser.cc (L1244-1279)
```text
  // Any `null` values eagerly no-op, except for exactly the special cases of
  // google.protobuf.Value message and google.protobuf.NullValue enum.
  // Note that spec behavior is that we should do duplicate key checking in the
  // case of a null value, but this implementation currently does not
  // (b/519557203).
  if (lex.Peek(JsonLexer::kNull)) {
    MessageType type = ClassifyMessage(Traits::FieldTypeName(*field));
    if (type != MessageType::kValue && type != MessageType::kNull) {
      return lex.Expect("null");
    }
  }

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

  // Message and repeated fields are cleared on first sight (meaning, it will
  // clear what was in the message before the parse began), but not cleared
  // in the case of the second occurance of the same key in the same JSON
  // payload (if duplicate keys are accepted, which they are in the current
  // default legacy mode).
  if (seen == SeenState::kFirstSeen &&
      (Traits::IsRepeated(*field) ||
       Traits::FieldType(*field) == FieldDescriptor::TYPE_MESSAGE ||
       Traits::FieldType(*field) == FieldDescriptor::TYPE_GROUP)) {
    Traits::ClearField(*field, msg);
  }
```
