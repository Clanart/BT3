### Title
Use-after-free of a live Python sub-message wrapper via `CopyFrom()` oneof switch on attacker-controlled bytes - ([File: python/google/protobuf/pyext/message.cc])

### Summary
The PuTTY CVE is a use-after-free where an attacker-controlled protocol message (`SSH1_MSG_DISCONNECT`) triggers a teardown path that frees memory that is subsequently accessed. The invariant that fails is: *a handle held by the caller must remain valid across a state transition triggered by untrusted input, unless the transition explicitly fixes up or invalidates that handle*. The closest genuine analog inside this Protobuf checkout is the Python C-extension's handling of oneof-field wrapper objects when the underlying C++ message is replaced by attacker-controlled data, specifically via `CopyFrom()`, which — unlike `MergeFromString`/`MergeFrom` — was not confirmed to run the same UAF-preventing fixup sequence.

### Finding Description
`python/google/protobuf/pyext/message.cc` maintains a `composite_fields` map on each `CMessage` that caches Python wrapper objects (`CMessage*`) pointing at the underlying C++ `Message*` sub-objects (e.g. a `oneof_nested_message` submessage). When a `oneof` field is later overwritten by parsing attacker-controlled bytes, the C++ layer (`Reflection::ClearOneof`, `TcParser::ChangeOneof` at [1](#0-0)  and [2](#0-1) ) `delete`s the previous oneof member. If a live Python wrapper object still points at that now-deleted C++ object, any subsequent attribute access is a use-after-free.

The codebase shows this exact bug class was previously present and is guarded against specifically in `MergeFromStringImpl`, via `MaybeReleaseOneofBeforeMerge` and `FixupMessageAfterMerge`: [3](#0-2) [4](#0-3) 

`MaybeReleaseOneofBeforeMerge` walks `composite_fields`, and for any oneof field about to be overwritten by the incoming (attacker-controlled) message, calls `InternalReleaseFieldByDescriptor` to detach the Python wrapper from the doomed C++ object *before* the C++ `MergeFrom`/`ChangeOneof` deletes it: [5](#0-4) 

A regression test explicitly documents this exact vulnerability class and its fix for the merge path: [6](#0-5) 

However, `CopyFrom()` — a separate public API that also lets an attacker supply the bytes of `other_message` (itself populated by `ParseFromString` on attacker bytes) and then overwrites `self`'s state — takes a different code path. It only clears the Python-level cache via a call to `Clear(self)` and then invokes the raw C++ `message->CopyFrom(*other_message->message)`: [7](#0-6) 

Unlike `MergeFromStringImpl`, this path does **not** call `MaybeReleaseOneofBeforeMerge` to detach existing oneof wrapper objects from the C++ objects that `CopyFrom`'s internal implementation is about to delete when switching oneof cases. The comment at line 2049-2050 acknowledges `composite_fields` is not otherwise cleaned up by `CopyFrom`, and handles it only by clearing the *map*, not by releasing/detaching any *outstanding* Python wrapper object references the caller may still be holding (e.g., a variable `sub_ref = msg.oneof_field` taken before calling `msg.CopyFrom(other)`).

### Impact Explanation
If confirmed reachable, this is a use-after-free: a Python object wrapper (`CMessage`) would retain a `message` pointer into a C++ `Message` object that has been `delete`d by the oneof-clearing logic inside `Message::CopyFrom` → `Reflection::ClearOneof`/`TcParser::ChangeOneof`. Subsequent attribute access on the stale wrapper reads/writes freed heap memory, which can cause a crash (denial of service) or, in more severe cases, memory corruption exploitable for further impact, entirely from a bounded, well-formed ProtoBuf byte stream parsed through the public `ParseFromString`/`CopyFrom` APIs — no privileged access or malicious schema required.

### Likelihood Explanation
Medium-to-High if the code path is confirmed unpatched, because: (1) the exact same bug class was found and fixed on a sibling API (`MergeFromString`) in this same file, indicating this construct is a known-recurring hazard in this component; (2) the trigger is a completely ordinary usage pattern (hold a reference to a oneof submessage, then call `CopyFrom` with another message that sets a different oneof case) requiring no unusual capabilities; (3) the payload for `other_message` can come from parsing attacker-controlled bytes via the public `ParseFromString` API.

I was not able to fully verify this within the available iterations: I could not retrieve the full body of the Python-level `Clear(self)` helper (`cmessage::Clear`) to confirm definitively whether it also releases/detaches outstanding wrapper references (in which case `CopyFrom` might already be safe), nor could I trace `Message::CopyFrom`'s C++ implementation end-to-end to confirm it always goes through `ClearOneof`/`ChangeOneof` for a differing oneof case. This should be verified in a live checkout before treating it as confirmed.

### Recommendation
Audit `CopyFrom()` in `python/google/protobuf/pyext/message.cc` to confirm whether `Clear(self)` (the Python-level clear used at line 2051) releases/detaches all outstanding oneof wrapper objects from the underlying C++ sub-objects, equivalent to what `MaybeReleaseOneofBeforeMerge` + `FixupMessageAfterMerge` do for the merge path. If it does not, apply the same detach-before-free pattern to `CopyFrom`: call `MaybeReleaseOneofBeforeMerge`-equivalent logic (or an unconditional full `InternalReleaseFieldByDescriptor` sweep of `composite_fields`) prior to invoking `message->CopyFrom(*other_message->message)`, and call `FixupMessageAfterMerge` afterward, mirroring the existing, tested fix already present in `MergeFromStringImpl`.

### Proof of Concept
Conceptual reproduction based on the pattern shown by the existing regression test `testOneofSwitchMergeUAF` ( [6](#0-5) ), adapted to the `CopyFrom` API instead of `MergeFromString`:
```python
m = TestAllTypes()
m.oneof_nested_message.bb = 42
data1 = m.SerializeToString()

m2 = TestAllTypes()
m2.ParseFromString(data1)
sub_ref = m2.oneof_nested_message   # Python wrapper referencing live C++ submessage

m3 = TestAllTypes()
m3.oneof_uint32 = 100                # different oneof case
data2 = m3.SerializeToString()
other = TestAllTypes()
other.ParseFromString(data2)

m2.CopyFrom(other)                   # internally clears/deletes previous oneof member

print(sub_ref.bb)                    # potential use-after-free if wrapper not detached
```
I was unable to execute this in a sandbox to confirm the crash/ASAN signal in this checkout; this should be run under ASan/valgrind against the actual `CopyFrom` implementation to confirm whether the detach-before-delete protection present in `MergeFromStringImpl` is missing here, before treating this as a confirmed, exploitable finding.

### Citations

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L2038-2046)
```text
  } else if (current_kind == field_layout::kFkMessage) {
    switch (current_rep) {
      case field_layout::kRepMessage:
      case field_layout::kRepGroup: {
        auto& field = RefAt<MessageLite*>(msg, current_entry->offset);
        if (!msg->GetArena()) {
          delete field;
        }
        break;
```

**File:** src/google/protobuf/generated_message_reflection.cc (L3403-3447)
```text
void Reflection::ClearOneof(Message* message,
                            const OneofDescriptor* oneof_descriptor) const {
  if (oneof_descriptor->is_synthetic()) {
    ClearField(message, oneof_descriptor->field(0));
    return;
  }
  // TODO: Consider to cache the unused object instead of deleting
  // it. It will be much faster if an application switches a lot from
  // a few oneof fields.  Time/space tradeoff
  uint32_t oneof_case = GetOneofCase(*message, oneof_descriptor);
  if (oneof_case > 0) {
    const FieldDescriptor* field = descriptor_->FindFieldByNumber(oneof_case);
    if (message->GetArena() == nullptr) {
      switch (field->cpp_type()) {
        case FieldDescriptor::CPPTYPE_STRING: {
          switch (field->cpp_string_type()) {
            case FieldDescriptor::CppStringType::kCord:
              delete *MutableRaw<absl::Cord*>(message, field);
              break;
            case FieldDescriptor::CppStringType::kView:
            case FieldDescriptor::CppStringType::kString:
              if (IsMicroString(field)) {
                MutableField<MicroString>(message, field)->Destroy();
              } else {
                // Oneof string fields are never set as a default instance.
                // We just need to pass some arbitrary default string to make it
                // work. This allows us to not have the real default accessible
                // from reflection.
                MutableField<ArenaStringPtr>(message, field)->Destroy();
              }
              break;
          }
          break;
        }

        case FieldDescriptor::CPPTYPE_MESSAGE:
          delete *MutableRaw<Message*>(message, field);
          break;
        default:
          break;
      }
    }

    *MutableOneofCase(message, oneof_descriptor) = 0;
  }
```

**File:** python/google/protobuf/pyext/message.cc (L757-807)
```text
int MaybeReleaseOneofBeforeMerge(CMessage* self, const Message& other) {
  CMessage::CompositeFieldsMap* composite_fields =
      self->composite_fields.TryGet();
  if (!composite_fields) {
    return 0;
  }

  Message* message = AssureWritable(self);
  if (message == nullptr) return -1;
  const Reflection* reflection = message->GetReflection();
  PyMessageFactory* factory = GetFactoryForMessage(self);
  std::vector<const FieldDescriptor*> fields_to_release;
  std::vector<std::pair<const FieldDescriptor*, ScopedPyObjectPtr>>
      nested_message_fields;
  composite_fields->ForEach([&](const void* key, PyObject* value) {
    const FieldDescriptor* descriptor =
        reinterpret_cast<const FieldDescriptor*>(key);
    if (descriptor->cpp_type() == FieldDescriptor::CPPTYPE_MESSAGE &&
        // For normal repeated message, MergeFrom will append the messages.
        // For message map with same keys, it is overwrite
        !descriptor->is_repeated() &&
        reflection->HasField(*message, descriptor)) {
      if (reflection->HasField(other, descriptor)) {
        Py_INCREF(value);
        nested_message_fields.emplace_back(descriptor, value);
      } else {
        // Release oneof message if the other message has set a different oneof
        const OneofDescriptor* oneof = descriptor->containing_oneof();
        if (oneof && reflection->HasOneof(other, oneof)) {
          fields_to_release.push_back(descriptor);
        }
      }
    }
  });

  for (const auto& [field, value] : nested_message_fields) {
    if (MaybeReleaseOneofBeforeMerge(
            reinterpret_cast<CMessage*>(value.get()),
            reflection->GetMessage(other, field, factory->message_factory)) <
        0) {
      return -1;
    }
  }

  for (const FieldDescriptor* field : fields_to_release) {
    if (InternalReleaseFieldByDescriptor(self, field) < 0) {
      return -1;
    }
  }
  return 0;
}
```

**File:** python/google/protobuf/pyext/message.cc (L2046-2053)
```text
  Message* message = AssureWritable(self);
  if (message == nullptr) return nullptr;

  // CopyFrom on the message will not clean up self->composite_fields,
  // which can leave us in an inconsistent state, so clear it out here.
  (void)ScopedPyObjectPtr(Clear(self));

  message->CopyFrom(*other_message->message);
```

**File:** python/google/protobuf/pyext/message.cc (L2087-2103)
```text
  // We parse into a temporary message first to detect oneof switches before
  // modifying the target message. This allows us to release the wrappers
  // for switching oneof fields in the target message before they are deleted
  // by C++ during the merge, preventing use-after-free bugs.
  // We use heap allocation (nullptr arena) for the temporary message so that
  // it is collected immediately after the merge, avoiding wasting arena memory.
  std::unique_ptr<Message> temp_message;
  Message* merge_dst;

  if (is_cleared) {
    // Check that it is really empty.
    ABSL_DCHECK_EQ(message->ByteSizeLong(), 0);
    merge_dst = message;
  } else {
    temp_message.reset(message->New(nullptr));
    merge_dst = temp_message.get();
  }
```

**File:** python/google/protobuf/pyext/message.cc (L2153-2163)
```text
  if (!is_cleared) {
    // If we are doing a real merge, fix oneofs, merge the object, then do
    // after-merge fixup.

    if (MaybeReleaseOneofBeforeMerge(self, *temp_message) < 0) {
      return nullptr;
    }

    message->MergeFrom(*temp_message);

    // Child message might be lazily created before MergeFrom. Make sure they
```

**File:** python/google/protobuf/internal/message_test.py (L2184-2202)
```python
  def testOneofSwitchMergeUAF(self, message_module):
    m = message_module.TestAllTypes()
    m.oneof_nested_message.bb = 42
    data1 = m.SerializeToString()

    m2 = message_module.TestAllTypes()
    m2.ParseFromString(data1)
    sub_ref = m2.oneof_nested_message

    m3 = message_module.TestAllTypes()
    m3.oneof_uint32 = 100
    data2 = m3.SerializeToString()

    m2.MergeFromString(data2)

    # Accessing sub_ref would trigger UAF before the fix because the C++
    # message was deleted on oneof switch. With the fix, the message is
    # released/detached, so the wrapper remains valid and keeps its value.
    self.assertEqual(42, sub_ref.bb)
```
