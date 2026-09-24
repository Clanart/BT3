## Analysis

The ChakraCore CVE describes a JS-engine memory-corruption bug: attacker-controlled script content causes stale/incorrect handling of an in-memory object, leading to type confusion / use-after-free that is reachable for RCE. The transferable invariant is: *a long-lived handle/wrapper to a sub-object must be invalidated or kept consistent whenever the underlying storage backing that object is freed, replaced, or moved as a result of processing untrusted input.* For Protobuf, the closest actual analog is not in the wire parser itself (which has no such defect I could confirm) but in the **CPython C++ extension's object-caching layer**, where Python wrapper objects (`CMessage`, map/repeated containers) hold raw `Message*`/`ContainerBase*` pointers into C++-owned memory that is mutated by `MergeFrom`/`MergeFromString` on attacker-controlled bytes.

I found that the *oneof* case of this bug class was already identified and fixed upstream (see `MaybeReleaseOneofBeforeMerge` / `FixupMessageAfterMerge` in `python/google/protobuf/pyext/message.cc`, and the regression test `testOneofSwitchMergeUAF` in `python/google/protobuf/internal/message_test.py` explicitly documents "before the fix... the C++ message was deleted on oneof switch"). However, the **analogous case for message-valued map fields is explicitly acknowledged as unfixed** in `testMapMergeFrom`/`testMergeFrom` in the same test file, which skip the assertion for the C++ implementation with the comment that accessing the stale wrapper "is likely to cause a segmentation fault."

### Title
Use-after-free of cached Python map-value wrapper on `MergeFrom`/`MergeFromString` with overlapping map keys - (File: `python/google/protobuf/pyext/message.cc`)

### Summary
When a Python `CMessage` wrapping a C++ protobuf message has a cached Python object for a message-valued map entry (obtained via `msg.some_map[key]`), and the message is subsequently merged with attacker-controlled bytes via `MergeFromString` (or with another message via `MergeFrom`) that contains an entry for the same map key, the C++ map merge logic (`UntypedMapBase::UntypedMergeFrom` / `MapFieldBase::MergeFrom` in `src/google/protobuf/map.cc:38-93` and `src/google/protobuf/map_field.cc:36-38`) reconstructs/replaces the underlying map node rather than mutating it in place via `InsertOrReplaceNode`. The pre-existing cached Python wrapper (`CMessage`) for that map value still points at the old, now-freed node. This is architecturally the same class of failure as `MaybeReleaseOneofBeforeMerge`/`FixupMessageAfterMerge` already fix for oneof fields — but that fix path is scoped only to non-repeated message fields (`!descriptor->is_repeated()` check at `python/google/protobuf/pyext/message.cc:777`), explicitly excluding maps, whose entries are also stored via the `CompositeFieldsMap`/`SubMessagesMap` caching mechanism.

### Finding Description
- `MaybeReleaseOneofBeforeMerge` (`python/google/protobuf/pyext/message.cc:757-807`) walks `self->composite_fields` and releases/detaches cached Python wrappers only when `descriptor->cpp_type() == CPPTYPE_MESSAGE && !descriptor->is_repeated()`. Map fields are represented via `MapContainer`/child message wrappers reached differently and are not covered by this pre-merge release pass.
- `FixupMessageAfterMerge` (same file, lines 810-840) similarly only fixes up non-repeated message fields after merge.
- The actual merge is performed by `message->MergeFrom(*temp_message)` (line 2161) / `message->MergeFrom(*other_message->message)` (line 2008), which for map fields dispatches into `internal::MapFieldBase::MergeFrom` → `UntypedMapBase::UntypedMergeFrom` (`src/google/protobuf/map.cc:38`), which allocates **new nodes** (`AllocNode`) for every incoming entry and calls `InsertOrReplaceNode` (line 89), which for overlapping keys unlinks/frees the old node.
- A cached Python `CMessage` object obtained earlier via `msg.map_field[key]` (built by `BuildSubMessageFromPointer`, `python/google/protobuf/pyext/message.cc:2934-2957`, holding a raw `const Message* message` pointer) is not registered in `composite_fields` for release, and continues to reference the now-freed node's message object after the merge completes.
- This is explicitly documented as a live, unresolved hazard by the project's own test suite: `python/google/protobuf/internal/message_test.py:3153-3161` states "the C++ implementation will have deallocated the underlying message ... The line below is likely to cause a segmentation fault," and the assertion is only run for the pure-Python implementation (`if api_implementation.Type() != 'cpp':`).

### Impact Explanation
An application that (a) parses/merges an attacker-supplied Protobuf message into an existing message object containing a message-valued map field, and (b) retains a Python reference to a map entry obtained before the merge, will dereference freed C++ heap/arena memory on subsequent access to that cached Python object. Depending on allocator reuse this is a use-after-free that can lead to a crash (denial of service) or, in the worst case, memory corruption/type confusion consistent with the CWE-787/memory-corruption class described in the CVE, if the freed memory is reallocated with attacker-influenced content. This does not require unbounded input, a hostile schema, or privileged access — only a normal `ParseFromString`/`MergeFromString`/`MergeFrom` call with a supported public API and a map field of message type, matching the stated threat model of an ordinary client sending bounded Protobuf bytes.

### Likelihood Explanation
Moderate-to-High for applications matching the specific but common pattern of caching map-entry sub-message references across merge calls (e.g., iterative/streaming merge of updates keyed by ID) — this is a realistic and idiomatic Python protobuf usage pattern (see `testMapMergeFrom`/`testMergeFrom` for exactly this scenario, written by protobuf's own maintainers to characterize the bug). It is gated on the CPython C++-accelerated implementation (`api_implementation.Type() == 'cpp'`); pure-Python and upb-based implementations are unaffected per the tests reviewed. I could not verify (given tool/time limits) whether upb-backed CPython extension (`python/map.c`, `python/message.c`) has an equivalent gap, since its map caching (`PyUpb_Arena_CacheAdd`/`PyUpb_MapContainer_Reify`) appears architected differently and was not fully traced to a merge-time reallocation path.

### Recommendation
Extend the existing oneof pre-merge release/fixup machinery (`MaybeReleaseOneofBeforeMerge` and `FixupMessageAfterMerge`) to also cover message-valued map fields: before calling `message->MergeFrom(...)`, walk cached map-entry wrappers for keys present in both the destination and source map and release/detach them (mirroring `InternalReleaseFieldByDescriptor`), or after merge, re-resolve any live map-entry `CMessage` wrappers to their (possibly reallocated) underlying `Message*` the same way `FixupMessageAfterMerge` does for scalar message fields. Alternatively, change `UntypedMapBase::UntypedMergeFrom` to update existing nodes in place for overlapping keys rather than always allocating new nodes and calling `InsertOrReplaceNode`, if such semantics are compatible with the Map API contract.

### Proof of Concept
This exact scenario is already codified (but its assertion suppressed for the `cpp` API implementation) in the repository's own test suite: [1](#0-0) 

```python
msg2 = map_unittest_pb2.TestMap()
msg2.map_int32_int32[12] = 55
msg2.map_int64_int64[88] = 99
msg2.map_int32_foreign_message[222].c = 15
msg2.map_int32_foreign_message[222].d = 20
old_map_value = msg2.map_int32_foreign_message[222]   # cache a Python wrapper

msg2.MergeFrom(msg)  # msg also has key 222 set for map_int32_foreign_message

# old_map_value.c  # <-- accessing this after merge is a UAF in the C++ impl;
                    #     test explicitly skips this assertion for cpp:
                    #     "likely to cause a segmentation fault"
```
I was not able to run this reproduction in a live environment (no execution tooling available in this session); the assessment is based on static code tracing of `python/google/protobuf/pyext/message.cc`, `src/google/protobuf/map.cc`, `src/google/protobuf/map_field.cc`, and the project's own acknowledgment of the hazard in its test file. [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** python/google/protobuf/internal/message_test.py (L3122-3161)
```python
  def testMergeFrom(self):
    msg = map_unittest_pb2.TestMap()
    msg.map_int32_int32[12] = 34
    msg.map_int32_int32[56] = 78
    msg.map_int64_int64[22] = 33
    msg.map_int32_foreign_message[111].c = 5
    msg.map_int32_foreign_message[222].c = 10

    msg2 = map_unittest_pb2.TestMap()
    msg2.map_int32_int32[12] = 55
    msg2.map_int64_int64[88] = 99
    msg2.map_int32_foreign_message[222].c = 15
    msg2.map_int32_foreign_message[222].d = 20
    old_map_value = msg2.map_int32_foreign_message[222]

    msg2.MergeFrom(msg)
    # Compare with expected message instead of call
    # msg2.map_int32_foreign_message[222] to make sure MergeFrom does not
    # sync with repeated field and there is no duplicated keys.
    expected_msg = map_unittest_pb2.TestMap()
    expected_msg.CopyFrom(msg)
    expected_msg.map_int64_int64[88] = 99
    self.assertEqual(msg2, expected_msg)

    self.assertEqual(34, msg2.map_int32_int32[12])
    self.assertEqual(78, msg2.map_int32_int32[56])
    self.assertEqual(33, msg2.map_int64_int64[22])
    self.assertEqual(99, msg2.map_int64_int64[88])
    self.assertEqual(5, msg2.map_int32_foreign_message[111].c)
    self.assertEqual(10, msg2.map_int32_foreign_message[222].c)
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

**File:** python/google/protobuf/pyext/message.cc (L808-840)
```text
// After a Merge, visit every sub-message that was read-only, and
// eventually update their pointer if the Merge operation modified them.
void FixupMessageAfterMerge(CMessage* self) {
  CMessage::CompositeFieldsMap* composite_fields =
      self->composite_fields.TryGet();
  if (!composite_fields) {
    return;
  }
  PyMessageFactory* factory = GetFactoryForMessage(self);
  composite_fields->ForEach([&](const void* key, PyObject* value) {
    const FieldDescriptor* descriptor =
        reinterpret_cast<const FieldDescriptor*>(key);
    if (descriptor->cpp_type() == FieldDescriptor::CPPTYPE_MESSAGE &&
        !descriptor->is_repeated()) {
      CMessage* cmsg = reinterpret_cast<CMessage*>(value);
      if (cmsg->state != MESSAGE_UNPROMOTED) {
        return;
      }
      Message* message = AssureWritable(self);
      if (message == nullptr) return;
      const Reflection* reflection = message->GetReflection();
      if (reflection->HasField(*message, descriptor)) {
        // Message used to be a default instance, but is no longer. Get the new
        // pointer and record it.
        Message* mutable_message = reflection->MutableMessage(
            message, descriptor, factory->message_factory);
        cmsg->message = mutable_message;
        cmsg->state = MESSAGE_MUTABLE;
        FixupMessageAfterMerge(cmsg);
      }
    }
  });
}
```

**File:** python/google/protobuf/pyext/message.cc (L2001-2013)
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

  Py_RETURN_NONE;
```

**File:** python/google/protobuf/pyext/message.cc (L2153-2166)
```text
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

**File:** src/google/protobuf/map.cc (L38-93)
```text
void UntypedMapBase::UntypedMergeFrom(Arena* arena,
                                      const UntypedMapBase& other) {
  ABSL_DCHECK_EQ(arena, this->arena());
  if (other.empty()) return;

  // Do the merging in steps to avoid Key*Value number of instantiations and
  // reduce code duplication per instantation.
  NodeBase* nodes = nullptr;

  // First, allocate all the nodes without types.
  for (size_t i = 0; i < other.num_elements_; ++i) {
    NodeBase* new_node = AllocNode(arena);
    new_node->next = nodes;
    nodes = new_node;
  }

  // Then, copy the values.
  VisitValueType([&](auto value_type) {
    using Value = typename decltype(value_type)::type;
    NodeBase* out_node = nodes;

    // Get the ClassData once to avoid redundant virtual function calls.
    const internal::ClassData* class_data =
        std::is_same_v<MessageLite, Value>
            ? GetClassData(*other.GetValue<MessageLite>(other.begin().node_))
            : nullptr;

    for (auto it = other.begin(); !it.Equals(EndIterator()); it.PlusPlus()) {
      Value* out = GetValue<Value>(out_node);
      out_node = out_node->next;
      auto& in = *other.GetValue<Value>(it.node_);
      if constexpr (std::is_same_v<MessageLite, Value>) {
        class_data->PlacementNew(out, arena)->CheckTypeAndMergeFrom(in);
      } else {
        Arena::CreateInArenaStorage(out, arena, in);
      }
    }
  });

  // Finally, copy the keys and insert the nodes.
  VisitKeyType([&](auto key_type) {
    using Key = typename decltype(key_type)::type;
    for (auto it = other.begin(); !it.Equals(EndIterator()); it.PlusPlus()) {
      NodeBase* node = nodes;
      nodes = nodes->next;
      const Key& in = *other.GetKey<Key>(it.node_);
      Key* out = GetKey<Key>(node);
      if (!internal::InitializeMapKey(out, in, arena)) {
        Arena::CreateInArenaStorage(out, arena, in);
      }

      static_cast<KeyMapBase<Key>*>(this)->InsertOrReplaceNode(
          arena, static_cast<typename KeyMapBase<Key>::KeyNode*>(node));
    }
  });
}
```

**File:** src/google/protobuf/map_field.cc (L36-38)
```text
void MapFieldBase::MergeFrom(Arena* arena, const MapFieldBase& other) {
  MutableMap()->UntypedMergeFrom(arena, other.GetMap());
}
```
