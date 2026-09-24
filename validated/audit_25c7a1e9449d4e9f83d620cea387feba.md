No matches confirm that `FieldMaskUtil` has no concept of extensions at all — extensions are entirely absent from both the "merge"/allow side and the "trim"/deny side of the field-mask utilities.

### Title
FieldMaskUtil::TrimMessage silently preserves extension fields not covered by the mask, bypassing an allow-list-based attribute filter - (File: src/google/protobuf/util/field_mask_util.cc)

### Summary
`google::protobuf::util::FieldMaskUtil::TrimMessage()` is the Protobuf analog to the pattern OpenCTI used incorrectly: a generic "keep only allow-listed fields" filter that applications run on an attacker-supplied message before persisting/merging it. The internal `FieldMaskTree::TrimMessage()` walk only iterates `descriptor->field_count()` — i.e. the statically declared, non-extension fields of the message — and never consults `Reflection::ListFields()` or any extension range/registry. Any extension field present on the message (settable by any client that has the extension descriptor, exactly like a bounded ProtoJSON/binary payload from an ordinary client) is left completely untouched by trimming, regardless of the field mask contents.

### Finding Description
`FieldMaskTree::TrimMessage` (`src/google/protobuf/util/field_mask_util.cc:612-652`) is implemented as: [1](#0-0) 

The loop bound is `descriptor->field_count()`, which for proto2/proto3 messages only returns fields declared directly in the message (regular fields), not extension fields registered against that message's extension ranges. Extensions are looked up dynamically via `Reflection::FindKnownExtensionByNumber`/`ListFields`, and are never visited by this loop. Consequently, if a caller relies on `FieldMaskUtil::TrimMessage(mask, message)` (`src/google/protobuf/util/field_mask_util.cc:728-734`) as an allow-list enforcement mechanism — trimming a message coming from a client request down to only the fields the client is permitted to set, before merging it into a stored/trusted object — any extension field set by the client survives the trim unconditionally, because it is invisible to the field-counting loop that clears "not in mask" fields.

This mirrors the OpenCTI failure mode precisely: OpenCTI's allow/deny list was applied against a set of "known" attributes but a class of values (`external`, `otp_qr`, `otp_activated`, token) could reach the persisted object without being checked against that list. Here, the "checked surface" (`descriptor->field(i)` for `i` in `[0, field_count)`) structurally excludes an entire class of attacker-settable values (extensions) from the enforcement pass, exactly analogous to the missing check.

### Impact Explanation
If application code (a documented/expected use-case of `FieldMaskUtil`) uses `TrimMessage` as an authorization boundary — e.g., "only allow these fields to be updated by the user, discard everything else from the client-provided message before merging into the canonical record" — an attacker who knows or can guess an extension field number/descriptor registered on that message type can set values through that extension and have them survive trimming, then get merged/persisted by the following application logic (e.g. via `MergeMessageTo` or a direct field-by-field copy). This is a confidentiality/integrity issue class-matched to CVE-2025-24887 (C:L/I:L): unauthorized attribute modification through a mechanism specifically designed to prevent it.

### Likelihood Explanation
Requires: (1) the target message type supports extensions (proto2, or explicit extension ranges), (2) the application uses `FieldMaskUtil::TrimMessage` (or the equivalent tree walk) as its sole enforcement mechanism for a field-level allow-list, and (3) the attacker has visibility into a usable extension. This is a plausible but non-default usage pattern — `FieldMaskUtil` documentation does not explicitly claim extension coverage, and most FieldMask update-mask use cases (e.g., Google API-style "update_mask") are protobuf3, which lacks extensions, reducing likelihood. Given these preconditions, I rate this Medium/likelihood-moderate, consistent with treating it as the strongest available analog rather than a certain, broadly-exploitable defect.

### Recommendation
Document explicitly in `field_mask_util.h` that `TrimMessage`/`MergeMessageTo` do not operate on extension fields, and that FieldMask-based allow-listing must not be relied upon as a security boundary for proto2 messages with extensions. Alternatively, extend `FieldMaskTree::TrimMessage` to also walk `Reflection::ListFields()` and clear any extension field descriptors not present in the tree, matching the enforcement behavior applied to regular fields.

### Proof of Concept
Conceptual reproduction (not run, provided as a minimal illustration mirroring `field_mask_util_test.cc` patterns):
```cpp
// Suppose 'TestAllExtensions' has extension ranges and 'my_extension' is a
// registered int32 extension field.
FieldMask mask;
FieldMaskUtil::FromString("optional_int32", &mask);  // allow-list: only this field

TestAllExtensions msg;
msg.set_optional_int32(1);           // allowed field, from client
msg.SetExtension(my_extension, 42);  // NOT in allow-list, from client

bool modified = FieldMaskUtil::TrimMessage(mask, &msg);
// Expected (secure) behavior: my_extension is cleared because it's not in the mask.
// Actual behavior: msg.GetExtension(my_extension) == 42 still, because
// FieldMaskTree::TrimMessage never iterates extension fields
// (src/google/protobuf/util/field_mask_util.cc:612-652 only loops
// descriptor->field(index) for index < field_count()).
```
I was unable to execute this against the actual build in this environment (no test runner available here); this should be validated with a Devin session running `field_mask_util_test.cc`-style unit tests against a proto2 message with a registered extension to confirm the extension value is retained after `TrimMessage`.

### Citations

**File:** src/google/protobuf/util/field_mask_util.cc (L612-631)
```text
bool FieldMaskTree::TrimMessage(const Node* node, Message* message) {
  ABSL_DCHECK(!node->children.empty());
  const Reflection* reflection = message->GetReflection();
  const Descriptor* descriptor = message->GetDescriptor();
  const int32_t field_count = descriptor->field_count();
  bool modified = false;
  for (int index = 0; index < field_count; ++index) {
    const FieldDescriptor* field = descriptor->field(index);
    auto it = node->children.find(field->name());
    if (it == node->children.end()) {
      if (field->is_repeated()) {
        if (reflection->FieldSize(*message, field) != 0) {
          modified = true;
        }
      } else {
        if (reflection->HasField(*message, field)) {
          modified = true;
        }
      }
      reflection->ClearField(message, field);
```
