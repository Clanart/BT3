## Analysis Result [1](#0-0) 

### Title
Potential use-after-free of cached Python wrapper objects during oneof-switching `ParseFromString` (direct-parse path) — ([File: python/google/protobuf/pyext/message.cc])

### Summary
libexpat's CVE-2026-56412 is a residual bug from an incomplete depth/state-tracking fix: `doCdataSection` didn't tag `XML_TOK_DATA_CHARS` for handler-call-depth tracking, so a policy-violation path could re-enter user code and free memory still referenced by the outer call frame — a **specific code path that bypasses a general-purpose protection mechanism that was added for the same bug class elsewhere**. The transferable invariant is: *"when attacker-controlled input triggers a state transition that frees an object, every live reference to that object held by an outer/collaborating layer must first be detached — and every code path that can trigger the transition must run through that detachment logic, not just the common one."*

In this Protobuf checkout, the CPython C-extension (`pyext`) implements exactly this detach-before-free protection for oneof switches triggered by parsing untrusted bytes, but the protection is implemented as a two-phase dance (parse into a temporary message, diff, detach, then merge) that is only exercised for the `MergeFromString` code path. The `ParseFromString` code path (`is_cleared=true`) skips the temporary-message diff and calls `_InternalParse` **directly on `self->message`**, relying entirely on `Clear()` having already detached every cached Python wrapper.

### Finding Description
`MergeFromStringImpl` in [2](#0-1)  explicitly documents the bug class it defends against:
```
// We parse into a temporary message first to detect oneof switches before
// modifying the target message. This allows us to release the wrappers
// for switching oneof fields in the target message before they are deleted
// by C++ during the merge, preventing use-after-free bugs.
```
This is confirmed by the regression test `testOneofSwitchMergeUAF` [3](#0-2) , which states: *"Accessing sub_ref would trigger UAF before the fix because the C++ message was deleted on oneof switch."* The underlying free happens in `TcParser::ChangeOneof`, which unconditionally `delete`s the previously-active oneof member when the wire data switches the active case [4](#0-3) . A live Python `CMessage` wrapper for that submessage (obtained e.g. via `m.oneof_nested_message`) still holds a raw `Message*` into freed memory unless it is proactively detached.

The fix, `MaybeReleaseOneofBeforeMerge` [5](#0-4) , walks `self->composite_fields` and detaches (reparents) any cached wrapper whose oneof case is about to be overwritten, **before** `message->MergeFrom(*temp_message)` is called. For `MergeFrom`/`MergeFromString`, the parse happens into an isolated `temp_message` first (line 2101), so the wire-driven `delete` in `ChangeOneof` never touches `self`'s pre-existing wrapped objects; the diff-then-release logic runs afterward, then `message->MergeFrom(*temp_message)` performs the actual free/replace on `self`, which by then has already been detached.

However, for `ParseFromString` (`is_cleared=true`), `merge_dst = message` (i.e., `self->message` itself), and `_InternalParse` runs **directly** on it [6](#0-5) . This means any oneof switch encountered while decoding attacker-controlled bytes runs `TcParser::ChangeOneof`'s `delete field` directly against `self->message`'s own field storage — the `MaybeReleaseOneofBeforeMerge` detachment pass is never invoked on this path (only `FixupMessageAfterMerge` runs afterward, at line 2122, which only *re-attaches* pointers for freshly-created message fields — it does not detach/rescue existing wrapper objects before a delete). Safety for this path depends entirely on `Clear(self)` (called by the wrapping `ParseFromString` Python method before `MergeFromStringImpl`, [7](#0-6) ) having already emptied `self->composite_fields`/`self->child_submessages` for every oneof-participating field.

### Impact Explanation
If `Clear()` does not fully detach every live Python wrapper for oneof-typed composite fields (for example, wrappers reachable only through nested submessages, extension fields participating in a oneof, or wrappers created via `WhichOneof`/attribute access after `Clear()` but before the subsequent `ParseFromString` bytes are consumed — a TOCTOU-style window within the same call), then decoding attacker-controlled bytes that select a *different* member of an existing oneof would cause `ChangeOneof`/`Destroy()` to free memory that a Python object still points to, producing a use-after-free reachable purely through the public `ParseFromString(bytes)` API with an ordinary trusted schema and bounded payload — matching the CVE's "policy-violation triggers free while an outer reference survives" shape exactly, and mirroring the already-documented `testOneofSwitchMergeUAF` bug class but through the one path (`ParseFromString`'s direct-parse) that the two-phase fix does not cover.

### Likelihood Explanation
Medium-to-low confidence without full source access: I could not verify inside this session whether `Clear()` (implementation not returned by search/index) unconditionally clears/detaches `composite_fields` and `child_submessages` for all oneof-participating fields before `MergeFromStringImpl(..., is_cleared=true)` runs `_InternalParse` directly on `self->message`. The existing regression test (`testOneofSwitchMergeUAF`) only exercises `MergeFromString`, not `ParseFromString`, so there is no test evidence that the same class of bug was checked/fixed for the direct-parse path. This should be treated as an unverified but structurally plausible gap rather than a proven vulnerability.

### Recommendation
- Verify (and if necessary fix) that `cmessage::Clear()` unconditionally empties `composite_fields`/`child_submessages` for every message before `ParseFromString`'s direct in-place `_InternalParse` call, for all reachable wrapper objects (including nested submessages and oneof/extension fields).
- Alternatively, make `ParseFromString` route through the same temp-message-then-diff-then-merge structure already used by `MergeFromString`, removing the `is_cleared` direct-parse shortcut, so a single hardened code path exists for "wire bytes may trigger a oneof switch that frees Python-visible state."
- Add a regression test analogous to `testOneofSwitchMergeUAF` but targeting `ParseFromString` specifically (obtain a wrapper via `m.oneof_nested_message`, call `Clear()`-adjacent operations, then `m.ParseFromString(data)` selecting a different oneof member, and assert the wrapper doesn't observe corrupted/freed memory).

### Proof of Concept
I was not able to execute code in this session (ask-only mode, no filesystem/test runner access), so no test was run and no result should be assumed. A conceptual reproduction to hand to an engineer with repo access:
```python
m = TestAllTypes()
m.oneof_nested_message.bb = 1     # creates & caches a wrapper for this oneof member
ref = m.oneof_nested_message       # attacker-independent local live reference

# attacker-controlled bytes selecting a DIFFERENT member of the same oneof
other = TestAllTypes(); other.oneof_uint32 = 100
data = other.SerializeToString()

m.ParseFromString(data)  # goes through is_cleared=True / direct-parse path
print(ref.bb)  # verify whether `ref` still safely reflects pre-Clear() state
               # or observes use-after-free/corrupted memory
```
This should be run under ASan/valgrind against the exact `Clear()` implementation to confirm whether a detachment gap exists on the `ParseFromString` path; I could not confirm this myself due to not having the `Clear()` source in reach during this session — a Devin session with full repository/build access would be needed to definitively confirm or refute this analog.

### Citations

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

**File:** python/google/protobuf/pyext/message.cc (L2074-2169)
```text
static PyObject* MergeFromStringImpl(CMessage* self, PyObject* arg,
                                     bool is_cleared) {
  Py_buffer data;
  if (PyObject_GetBuffer(arg, &data, PyBUF_SIMPLE) < 0) {
    return nullptr;
  }
  auto cleanup_data = absl::MakeCleanup([&data] { PyBuffer_Release(&data); });

  Message* message = AssureWritable(self);
  if (message == nullptr) {
    return nullptr;
  }

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

  PyMessageFactory* factory = GetFactoryForMessage(self);
  int depth = allow_oversize_protos
                  ? INT_MAX
                  : io::CodedInputStream::GetDefaultRecursionLimit();
  const char* ptr;
  internal::ParseContext ctx(
      depth, false, &ptr,
      absl::string_view(static_cast<const char*>(data.buf), data.len));

  ctx.data().pool = factory->pool->pool->get();
  ctx.data().factory = factory->message_factory;

  ptr = merge_dst->_InternalParse(ptr, &ctx);

  if (is_cleared) {
    // If we merged into the final destination, fix up now before we might have
    // an early exit.
    FixupMessageAfterMerge(self);
  }

  // Python makes distinction in error message, between a general parse failure
  // and in-correct ending on a terminating tag. Hence we need to be a bit more
  // explicit in our correctness checks.
  if (ptr == nullptr) {
    PyErr_Format(
        DecodeError_class, "Error parsing message with type '%s'",
        std::string(self->GetMessageClass()->message_descriptor->full_name())
            .c_str());
    return nullptr;
  }
  if (ctx.BytesUntilLimit(ptr) < 0) {
    // The parser overshot the limit.
    PyErr_Format(
        DecodeError_class,
        "Error parsing message as the message exceeded the protobuf limit "
        "with type '%s'",
        std::string(self->GetMessageClass()->message_descriptor->full_name())
            .c_str());
    return nullptr;
  }
  // ctx has an explicit limit set (length of string_view), so we have to
  // check we ended at that limit.
  if (!ctx.EndedAtLimit()) {
    PyErr_Format(DecodeError_class,
                 "Unexpected end-group tag: Not all data was converted");
    return nullptr;
  }

  if (!is_cleared) {
    // If we are doing a real merge, fix oneofs, merge the object, then do
    // after-merge fixup.

    if (MaybeReleaseOneofBeforeMerge(self, *temp_message) < 0) {
      return nullptr;
    }

    message->MergeFrom(*temp_message);

    // Child message might be lazily created before MergeFrom. Make sure they
    // are mutable at this point if child messages are really created.
    FixupMessageAfterMerge(self);
  }

  return PyLong_FromLong(data.len);
}
```

**File:** python/google/protobuf/pyext/message.cc (L2175-2180)
```text
static PyObject* ParseFromString(CMessage* self, PyObject* arg) {
  if (ScopedPyObjectPtr(Clear(self)) == nullptr) {
    return nullptr;
  }
  return MergeFromStringImpl(self, arg, true);
}
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

**File:** src/google/protobuf/generated_message_tctable_lite.cc (L2038-2052)
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
      }
      default:
        internal::Unreachable();
        return;
    }
  }
```
