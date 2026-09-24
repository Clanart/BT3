### Title
Missing null check on `MessageFactory::GetPrototype()` result before dereference in `ExtensionSet::MutableMessage()` - ([File: src/google/protobuf/extension_set_heavy.cc])

### Summary
`ExtensionSet::MutableMessage()` calls `factory->GetPrototype(descriptor->message_type())` and immediately dereferences the result via `prototype->New(arena)` without checking whether `GetPrototype()` returned `nullptr`. The `MessageFactory::GetPrototype()` contract explicitly documents that it "will return nullptr if the descriptor passed in is not supported" [1](#0-0) , yet this call site skips the check that sibling call sites in the same file perform.

### Finding Description
This is a structural analog of CVE-2024-26612: a pointer returned from a "may-be-null/may-be-error" accessor is dereferenced before the null/error state is checked. In the kernel bug, `cache` from `fscache_get_cache()`-style lookup is used prior to the `IS_ERR_OR_NULL()` check. In protobuf, `MessageFactory::GetPrototype()` plays the analogous "may return null" role, and `MutableMessage()` violates the invariant by dereferencing first: [2](#0-1) 

Compare this to the two other call sites in the same translation unit that correctly guard the same call:
- `ExtensionSet::AddMessage()` explicitly checks `ABSL_CHECK(prototype != nullptr);` before use [3](#0-2) .
- `DescriptorPoolExtensionFinder::Find()` explicitly checks `ABSL_CHECK_NE(prototype, nullptr)` with a descriptive message before use [4](#0-3) .

`MutableMessage()` is the odd one out — it neither has a `CHECK` nor any `nullptr` guard, meaning that if `factory->GetPrototype(descriptor->message_type())` returns `nullptr`, the subsequent `prototype->New(arena)` is an unconditional null-pointer dereference.

### Impact Explanation
If reached, this results in a null-pointer dereference / crash (a `CHECK`-less `SEGV`), analogous to the kernel Oops fixed by CVE-2024-26612. That CVE was rated Medium (Availability impact only, no confidentiality/integrity impact) — the protobuf analog would carry the same severity ceiling: a crash of the parsing process, not memory corruption or information disclosure, since no controlled write occurs (the dereference happens at offset 0 inside `New()`'s implicit `this`).

### Likelihood Explanation
I was **not able to fully confirm reachability** under the stated attacker model (an ordinary client sending bounded, schema-trusted binary protobuf/ProtoJSON through a public parse API) within the available iterations. `MessageFactory::generated_factory()->GetPrototype()` only returns `nullptr` when the descriptor's file is not in `DescriptorPool::generated_pool()` [5](#0-4) . During ordinary generated-code parsing, extensions are resolved through the same generated pool/factory pair, so this branch is not normally reachable by wire bytes alone — it requires an application to construct an `ExtensionSet`/`ExtensionInfo` combination that mismatches factory and descriptor pool (e.g., mixing a `DynamicMessageFactory` configured against one pool with extension descriptors resolved from a different pool, or a custom `MessageFactory` subclass that legitimately returns `nullptr` per the documented contract). This is closer to an internal-API/configuration-misuse scenario than a pure "attacker sends bytes" scenario, and I could not trace, within the remaining budget, a call path from `ParseField`/`ParseFieldWithExtensionInfo` in `extension_set.h`/`extension_set_inl.h` that would let field-number/wire-type values chosen entirely by an attacker force a factory/pool mismatch when the application uses only the single trusted generated factory and pool (the common case). Given the rules exclude findings that require malicious/mismatched pools, custom hostile `MessageFactory` implementations, or internal-API misuse, this finding should be treated as **not confidently established** as attacker-reachable through the standard public parse surface, though the code-level invariant violation itself is real and clearly demonstrated by the inconsistency with the two guarded sibling call sites.

### Recommendation
Add the same guard used elsewhere in the file before dereferencing the prototype:
```cpp
const MessageLite* prototype =
    factory->GetPrototype(descriptor->message_type());
ABSL_CHECK(prototype != nullptr);
extension->ptr.message_value = prototype->New(arena);
```
This matches the pattern already established in `ExtensionSet::AddMessage()` and `DescriptorPoolExtensionFinder::Find()` in the same file, closing the "check-after-use" gap analogous to the kernel `fscache_put_cache()` fix.

### Proof of Concept
A concrete, minimal, trusted-schema/bounded-payload reproduction requires constructing an `ExtensionSet::MutableMessage()` call where `factory->GetPrototype(descriptor->message_type())` legitimately returns `nullptr` (e.g., a `MessageFactory` subclass returning `nullptr` for an unsupported type, paired with an extension field descriptor of message type routed to that factory) and then invoking the mutable-extension accessor. I was unable to construct and verify this end-to-end within the current tool-call budget, so **no test was run and no crash was reproduced**; this should be validated with a local build before being escalated, given the reachability uncertainty noted above.

### Citations

**File:** src/google/protobuf/message.h (L1561-1567)
```text
  // Some implementations do not support all types.  GetPrototype() will
  // return nullptr if the descriptor passed in is not supported.
  //
  // This method may or may not be thread-safe depending on the implementation.
  // Each implementation should document its own degree thread-safety.
  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD virtual const Message* GetPrototype(
      const Descriptor* type) = 0;
```

**File:** src/google/protobuf/message.h (L1569-1578)
```text
  // Gets a MessageFactory which supports all generated, compiled-in messages.
  // In other words, for any compiled-in type FooMessage, the following is true:
  //   MessageFactory::generated_factory()->GetPrototype(
  //     FooMessage::descriptor()) == FooMessage::default_instance()
  // This factory supports all types which are found in
  // DescriptorPool::generated_pool().  If given a descriptor from any other
  // pool, GetPrototype() will return nullptr.  (You can also check if a
  // descriptor is for a generated message by checking if
  // descriptor->file()->pool() == DescriptorPool::generated_pool().)
  //
```

**File:** src/google/protobuf/extension_set_heavy.cc (L121-143)
```text
MessageLite* ExtensionSet::MutableMessage(Arena* arena,
                                          const FieldDescriptor* descriptor,
                                          MessageFactory* factory) {
  Extension* extension;
  if (MaybeNewExtension(arena, descriptor->number(), descriptor, &extension)) {
    extension->type = descriptor->type();
    ABSL_DCHECK_EQ(cpp_type(extension->type), FieldDescriptor::CPPTYPE_MESSAGE);
    extension->is_repeated = false;
    extension->is_pointer = true;
    extension->is_packed = false;
    const MessageLite* prototype =
        factory->GetPrototype(descriptor->message_type());
    extension->is_lazy = false;
    extension->ptr.message_value = prototype->New(arena);
    extension->is_cleared = false;
    return extension->ptr.message_value;
  } else {
    ABSL_DCHECK_TYPE(*extension, OPTIONAL, MESSAGE);
    extension->is_cleared = false;
    ABSL_DCHECK(!extension->is_lazy);
    return extension->ptr.message_value;
  }
}
```

**File:** src/google/protobuf/extension_set_heavy.cc (L216-220)
```text
  if (result == nullptr) {
    const MessageLite* prototype = nullptr;
    if (extension->ptr.repeated_message_value->empty()) {
      prototype = factory->GetPrototype(descriptor->message_type());
      ABSL_CHECK(prototype != nullptr);
```

**File:** src/google/protobuf/extension_set_heavy.cc (L257-262)
```text
    if (extension->cpp_type() == FieldDescriptor::CPPTYPE_MESSAGE) {
      const MessageLite* prototype =
          factory_->GetPrototype(extension->message_type());
      ABSL_CHECK_NE(prototype, nullptr)
          << "Extension factory's GetPrototype() returned nullptr; extension: "
          << extension->full_name();
```
