Based on my investigation, this is the key finding.

### Title
Use-After-Free via stale Python wrapper on message-map value overwrite during `MergeFrom`/`MergeFromString` - (File: `python/google/protobuf/pyext/message.cc`, `python/google/protobuf/pyext/map_container.cc`)

### Summary
The C++-backed Python protobuf implementation (`pyext`) contains an explicit, tested fix (`MaybeReleaseOneofBeforeMerge`/`FixupMessageAfterMerge`) for use-after-free when a `oneof` submessage held by a live Python wrapper is destroyed during `MergeFrom`. However, that protection is scoped only to non-repeated composite fields [1](#0-0) , and is never applied to message-valued map entries, even though the underlying C++ map merge can overwrite (replace) existing entries when both sides only partially match.

### Finding Description
`MergeFrom(CMessage*, PyObject*)` and `MergeFromStringImpl` guard against the documented UAF class by calling `MaybeReleaseOneofBeforeMerge` before invoking C++ `Message::MergeFrom`, specifically to detach Python wrappers for submessages that are about to be deleted due to a oneof case switch [2](#0-1) [3](#0-2) . The comment explicitly states this exists to prevent "use-after-free bugs" from oneof switches [4](#0-3) , and there is a dedicated regression test (`testOneofSwitchMergeUAF`) confirming this exact bug class was previously exploitable and is now fixed for singular fields [5](#0-4) .

Critically, `MaybeReleaseOneofBeforeMerge` explicitly excludes repeated fields (`!descriptor->is_repeated()`) from its traversal [6](#0-5) , meaning message-map fields (which are represented as repeated `FieldDescriptor`s) are never visited by this release logic, nor is `FixupMessageAfterMerge` applied to entries obtained through the map. Separately, the existing Python test suite documents that `MergeFrom` on message-valued maps can *overwrite* an existing map value's C++ storage on key collision rather than merge in place: `testMergeFrom` in `map_unittest_pb2` explicitly notes "During the call to MergeFrom(), the C++ implementation will have deallocated the underlying message, but this is very difficult to detect properly. The line below is likely to cause a segmentation fault," guarding the check behind `if api_implementation.Type() != 'cpp'` [7](#0-6) . This same reasoning is echoed in `testMapMergeFrom`'s "Test overwrite message value map" case, which shows `MergeFromString` replacing map-entry contents when the same key exists in both source and destination [8](#0-7) .

Combined, this means: a Python application that (a) reads a `MessageMap` value into a local variable (obtaining a `CMessage` wrapper cached in `self->child_submessages`, see `BuildSubMessageFromPointer` [9](#0-8) ), then (b) calls `MergeFrom`/`MergeFromString`/`ParseFromString` with attacker-controlled bytes containing the same map key, can end up with that cached wrapper pointing at freed/replaced C++ memory, because the release/fixup machinery that exists specifically for this failure mode is bypassed for all `is_repeated()` fields, including maps.

### Impact Explanation
If exploitable, subsequent Python-level access to the stale map-value wrapper (e.g., `old_map_value.c` in the test) reads or writes through a dangling `Message*` pointer, resulting in memory corruption/crash and potential information disclosure — directly analogous in effect to the underlying CVE-2025-43216 pattern (attacker-supplied content triggering a use-after-free reachable through routine "process this input" API calls, with no privileged access needed). The consuming-application exposure model matches the report's assumption: any Python service that parses attacker-supplied Protobuf bytes via `MergeFromString`/`ParseFromString`/`MergeFrom` into a message containing a `map<K, Message>` field is exposed if it also retains any live reference to a map entry.

### Likelihood Explanation
Medium confidence, not fully proven. I confirmed the *existence and scope* of the singular-field fix and the *explicit code-level exclusion* of repeated/map fields from that fix (`!descriptor->is_repeated()`), and I found first-party test comments in this checkout stating that C++-backed `MergeFrom` "deallocated the underlying message" for map values in a way the Python test suite itself avoids exercising ("very difficult to detect properly... likely to cause a segmentation fault," guarded by `if api_implementation.Type() != 'cpp'`). This is strong circumstantial evidence of a live gap, but I was not able to trace the exact `MapFieldBase::MergeFrom` C++ implementation to confirm precisely when/whether it reallocates versus merges-in-place for existing keys with message values, nor build/run a reproduction to observe an actual crash under ASan. The test author's own caution (skipping the check for the `cpp` implementation) suggests they believe a real memory-safety issue exists but is impractical to assert deterministically in CI.

### Recommendation
1. Extend `MaybeReleaseOneofBeforeMerge` (or add an analogous routine) to also visit message-valued map/repeated composite fields present in `composite_fields`/child wrapper caches, releasing/detaching any live `CMessage` wrapper for a map entry whose key exists in both source and destination before invoking `MapFieldBase::MergeFrom`.
2. Extend `FixupMessageAfterMerge` to re-resolve pointers for map-entry wrappers after merge, symmetric to what is already done for singular message fields.
3. Un-skip/re-enable the `cpp`-implementation branch of `testMergeFrom` in `python/google/protobuf/internal/message_test.py` (or add a dedicated ASan-instrumented regression test analogous to `testOneofSwitchMergeUAF`, but for message-map values) once the fix lands, to make the invariant enforceable in CI going forward.

### Proof of Concept
Not executed (no code-execution environment in this session). Suggested reproduction outline for a Devin session with build access:
```python
from google.protobuf import api_implementation
assert api_implementation.Type() == 'cpp'  # or 'upb', re-check equivalent path
import map_unittest_pb2 as m

msg = m.TestMap()
msg.map_int32_foreign_message[222].c = 10

other = m.TestMap()
other.map_int32_foreign_message[222].d = 20

old_ref = msg.map_int32_foreign_message[222]   # cache Python wrapper
msg.MergeFromString(other.SerializeToString())  # attacker-controlled bytes, same key

# Under ASan/valgrind, accessing old_ref.c here should be flagged as
# heap-use-after-free if the underlying entry was deallocated/replaced
# rather than merged in place.
print(old_ref.c)
```
This should be run under AddressSanitizer against a `cpp`-implementation build to confirm whether the map-entry C++ storage is actually freed/reallocated (not just logically overwritten) on merge, which is the open question left unverified in this analysis.

### Citations

**File:** python/google/protobuf/pyext/message.cc (L774-789)
```text
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
```

**File:** python/google/protobuf/pyext/message.cc (L2001-2011)
```text
  Message* message = AssureWritable(self);
  if (message == nullptr) return nullptr;

  if (MaybeReleaseOneofBeforeMerge(self, *other_message->message) < 0) {
    return nullptr;
  }

  message->MergeFrom(*other_message->message);
  // Child message might be lazily created before MergeFrom. Make sure they
  // are mutable at this point if child messages are really created.
  FixupMessageAfterMerge(self);
```

**File:** python/google/protobuf/pyext/message.cc (L2087-2117)
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
```

**File:** python/google/protobuf/pyext/message.cc (L2934-2957)
```text
CMessage* CMessage::BuildSubMessageFromPointer(
    const FieldDescriptor* field_descriptor, const Message* sub_message,
    CMessageClass* message_class, MessageMutabilityState state) {
  if (PyObject* value =
          this->child_submessages.Get()->Get(sub_message, nullptr)) {
    return reinterpret_cast<CMessage*>(value);
  }

  CMessage* cmsg = cmessage::NewEmptyMessage(message_class);

  if (cmsg == nullptr) {
    return nullptr;
  }
  cmsg->message = sub_message;
  Py_INCREF(this);
  cmsg->parent = this;
  cmsg->parent_field_descriptor = field_descriptor;
  cmsg->state = this->state == MESSAGE_FROZEN ? MESSAGE_FROZEN : state;
  cmessage::SetSubmessage(this, cmsg);
  if (state == MESSAGE_MUTABLE) {
    cmsg->has_mutable_map_ancestor =
        this->has_mutable_map_ancestor || field_descriptor->is_map();
  }
  return cmsg;
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

**File:** python/google/protobuf/internal/message_test.py (L3152-3161)
```python
    self.assertFalse(msg2.map_int32_foreign_message[222].HasField('d'))
    if api_implementation.Type() != 'cpp':
      # During the call to MergeFrom(), the C++ implementation will have
      # deallocated the underlying message, but this is very difficult to detect
      # properly. The line below is likely to cause a segmentation fault.
      # With the Python implementation, old_map_value is just 'detached' from
      # the main message. Using it will not crash of course, but since it still
      # have a reference to the parent message I'm sure we can find interesting
      # ways to cause inconsistencies.
      self.assertEqual(15, old_map_value.c)
```

**File:** python/google/protobuf/internal/message_test.py (L3231-3238)
```python
    # Test overwrite message value map
    msg = map_unittest_pb2.TestMap()
    msg.map_int32_foreign_message[222].c = 123
    msg2 = map_unittest_pb2.TestMap()
    msg2.map_int32_foreign_message[222].d = 20
    msg.MergeFromString(msg2.SerializeToString())
    self.assertEqual(msg.map_int32_foreign_message[222].d, 20)
    self.assertNotEqual(msg.map_int32_foreign_message[222].c, 123)
```
