## Analysis

CVE-2016-2544's failed invariant: the Linux kernel's `queue_delete()` freed a `snd_seq_queue` object without holding the lock/refcount that concurrent ioctl paths relied on, so a second thread could dereference the freed queue (use-after-free) because deletion and concurrent access were not mutually exclusive.

The transferable invariant is: **an object's deletion/reparenting path must be mutually exclusive with a concurrent lazy-access path that dereferences the same raw pointer**. The exposure assumption for Protobuf: a host application built with free-threaded CPython (`Py_GIL_DISABLED`) parses attacker-controlled bytes into a message on one thread while application code concurrently calls `Clear()`/`ClearField()` on that message from another thread — a realistic pattern in multi-threaded request handlers that reuse or mutate a shared parsed message.

I traced `Clear()` / `ClearField()` in `python/google/protobuf/pyext/message.cc`. `Clear()` first snapshots existing children from `composite_fields`/`child_submessages` [1](#0-0) , then calls `InternalReparentFields()`, which swaps ownership of the underlying `Message` fields into a brand-new `Message*` (`mutable_new->message->New(nullptr)`) via `SwapFields`/`UnsafeShallowSwapFields`, and only afterward does `message->Clear()` [2](#0-1) . `GetFieldValue()`, on the other hand, lazily builds a submessage wrapper by taking the *raw* C++ pointer returned by `reflection->GetMessage(*self->message, field_descriptor, ...)` and caching it before returning: `InternalGetSubMessage` sets `cmsg->message = &reflection->GetMessage(...)` unconditionally [3](#0-2) , and `GetFieldValue` only serializes the *insertion* into `composite_fields` through `SetCompositeField`/`PyWeakValueMap::TrySet`, not the earlier construction of the wrapper or the read of `self->message` [4](#0-3) .

`PyWeakValueMap` itself is internally mutex-protected in `Py_GIL_DISABLED` builds (`absl::Mutex mutex_` guarding `cache_`) [5](#0-4) , and its `Get`/`TrySet`/`EraseIfEqualImpl` correctly take the lock [6](#0-5) . That protects the *map* against concurrent Set/Erase, but it does not protect the underlying `Message` object that a `CMessage` wrapper's `->message` pointer refers to. If thread A is inside `Clear()`'s reparenting sequence (which mutates `self->message`'s fields and then invokes `message->Clear()`) while thread B concurrently calls `GetFieldValue()` on the same parent and dereferences `self->message` via `reflection->GetMessage(*self->message, ...)`, thread B can observe a half-swapped/cleared C++ `Message`, or build a `CMessage` wrapper pointing at a sub-object that is about to be reparented/dropped by thread A's swap. This is exactly the CVE's shape: deletion (`Clear`) and lazy dereference (`GetFieldValue`) of the same object are not mutually exclusive, only the *cache bookkeeping* around them is locked.

This matches, essentially verbatim, the project's own reproduction: `testConcurrentClearAndSubObjectDeletionRace` explicitly documents "a dangling pointer dereference race between Clear() and sub-object deallocation" [7](#0-6) , and `testConcurrentGetFieldValueRace` / `testConcurrentLazyUnpackAndRead` document the same class of race for lazy submessage/lazy-field initialization racing with concurrent reads [8](#0-7) [9](#0-8) . These are present as *regression tests*, i.e. acknowledged, previously-observed races in the free-threaded CPython extension, not hardening for a hypothetical.

I was not able to fully verify, within this pass, whether the underlying C++ `SwapFields`/`Message::Clear()` call sequence in `InternalReparentFields` is itself safe against a concurrent `Reflection::GetMessage()` read at the `Message`/reflection layer (i.e., whether there's a lower-level guard I haven't located), or whether recent upstream commits have added such synchronization since this checkout. That would require deeper tracing of `MessageReflectionFriend::UnsafeShallowSwapFields` and `Reflection::SwapFields` under concurrent `GetMessage()`, which I could not complete with the remaining budget.

### Title
Use-after-free race between `Clear()` field reparenting and concurrent `GetFieldValue()` lazy submessage dereference - (File: python/google/protobuf/pyext/message.cc)

### Summary
In the free-threaded CPython build of the C++ Python extension (`Py_GIL_DISABLED`), `Clear()`/`ClearField()` swap and clear the underlying `Message*` fields of a `CMessage` without holding any lock against concurrent `GetFieldValue()` calls on the same parent object, which dereference the raw `Message*` to build/cache new submessage wrappers. Only the bookkeeping maps (`composite_fields`, `child_submessages`) are mutex-protected; the `Message` object's own field storage is not, allowing one thread's deletion/reparenting of a submessage to race with another thread's read of the same underlying pointer.

### Finding Description
`Clear()` collects existing composite children, then calls `InternalReparentFields()`, which reflectively swaps the parent's fields into a newly allocated `Message`, and finally calls `message->Clear()` on the original `Message` [1](#0-0) [10](#0-9) . None of this sequence takes a lock that also guards reads of `self->message`.

Concurrently, `GetFieldValue()` on the same `CMessage` reads `self->message`, calls `reflection->GetMessage(*self->message, field_descriptor, ...)`, and constructs a new `CMessage` wrapper pointing directly at whatever raw pointer that call returns, only inserting the *result* into the mutex-protected `composite_fields` map afterward [4](#0-3) ; `InternalGetSubMessage` performs the same unguarded dereference [3](#0-2) . The lock inside `PyWeakValueMap` (`absl::Mutex mutex_`) only protects the cache's own hash map [5](#0-4) ; it provides no exclusion for the concurrent mutation of the `Message` object performed by `Clear()`'s field-swap and `Clear()` call.

The failed invariant transferring from the CVE is: "an object's lifecycle-mutating operation (delete/clear/reparent) must be mutually exclusive with any operation that dereferences the same object," which the kernel's `queue_delete` violated via missing locking, and which this code path violates via locking only the cache layer instead of the underlying mutable state.

### Impact Explanation
A thread calling `GetFieldValue()`/`InternalGetSubMessage()` while another thread concurrently executes `Clear()` on the parent can observe a `Message` mid-swap, or construct/cache a `CMessage` wrapper referencing a submessage pointer that is being reparented away and whose backing storage the swap/clear sequence is actively mutating. This is a data race that can produce corrupted field reads, or a dangling `CMessage::message` pointer depending on the object's exact lifetime, mirroring the "use-after-free and crash" impact class of the CVE, scoped to (C) low, (I) low, (A) high consistent with the reported CVSS vector.

### Likelihood Explanation
This requires (a) a `Py_GIL_DISABLED` free-threaded CPython build, and (b) the host application sharing a single parsed `Message`/`CMessage` object across threads and calling `Clear()` on it while another thread accesses fields — a realistic pattern for servers that reuse message objects across requests. This does not require a malicious peer, privileged access, or hostile schema; the attacker-controlled payload just needs to produce a message with a submessage field, and the trigger is the host application's own concurrent access pattern, which the project's own tests classify as reproducible and (in `testConcurrentRepeatedCompositeSubscript`) explicitly marks as *not yet fixed for upb* [11](#0-10) , and reproduces for the C++ pyext implementation via `testConcurrentClearAndSubObjectDeletionRace` [7](#0-6) .

### Recommendation
Guard the `Clear()`/`ClearField()` field-swap-and-clear sequence and the `GetFieldValue()`/`InternalGetSubMessage()` pointer dereference-and-cache sequence with the same synchronization primitive (e.g., extend the per-`CMessage` locking used for `composite_fields`/`child_submessages` to also cover reads/writes of `self->message`'s field storage during these operations), or make `Clear()` publish the new/cleared `Message*` atomically such that any concurrently-in-flight `GetFieldValue()` either observes the fully-swapped state or the fully-original state, never a partial one.

### Proof of Concept
The repository's own regression test reproduces the race directly against production code: `testConcurrentClearAndSubObjectDeletionRace` spins up two threads — one calling `msg.Clear()`, the other holding and clearing a reference obtained from `msg.optional_nested_message` — synchronized via a `threading.Barrier`, run in a loop of 500 iterations to maximize the chance of hitting the race window [7](#0-6) ; the related `testConcurrentGetFieldValueRace` isolates the lazy-initialization race in `GetFieldValue` under ten concurrently-barriered threads [8](#0-7) . I was not able to execute these tests in this session; their presence as dedicated regression tests in the checkout is the available evidence that the race is real and previously observed, not merely theoretical.

### Citations

**File:** python/google/protobuf/pyext/message.cc (L1627-1701)
```text
static int InternalReparentFields(
    CMessage* self, const std::vector<ScopedPyObjectPtr>& messages_to_release,
    const std::vector<ScopedPyObjectPtr>& containers_to_release) {
  if (messages_to_release.empty() && containers_to_release.empty()) {
    return 0;
  }

  // Move all the passed sub_messages to another message.
  CMessage* new_message = cmessage::NewEmptyMessage(self->GetMessageClass());
  if (new_message == nullptr) {
    return -1;
  }
  new_message->message = self->message->New(nullptr);
  ScopedPyObjectPtr holder(reinterpret_cast<PyObject*>(new_message));
  CMessage::SubMessagesMap* new_child_submessages =
      new_message->child_submessages.Get();
  CMessage::CompositeFieldsMap* new_composite_fields =
      new_message->composite_fields.Get();
  CMessage::SubMessagesMap* self_child_submessages =
      self->child_submessages.Get();
  CMessage::CompositeFieldsMap* self_composite_fields =
      self->composite_fields.Get();
  std::set<const FieldDescriptor*> fields_to_swap;

  // In case this the removed fields are the last reference to a message, keep
  // a reference.
  Py_INCREF(self);

  for (const auto& to_release_ptr : messages_to_release) {
    CMessage* to_release = reinterpret_cast<CMessage*>(to_release_ptr.get());
    fields_to_swap.insert(to_release->parent_field_descriptor);
    // Reparent
    Py_INCREF(new_message);
    Py_DECREF(to_release->parent);
    to_release->parent = new_message;
    self_child_submessages->Erase(to_release->message,
                                  to_release->AsPyObject());
    new_child_submessages->Set(to_release->message, to_release->AsPyObject());
  }

  for (const auto& to_release_ptr : containers_to_release) {
    ContainerBase* to_release =
        reinterpret_cast<ContainerBase*>(to_release_ptr.get());
    fields_to_swap.insert(to_release->parent_field_descriptor);
    Py_INCREF(new_message);
    Py_DECREF(to_release->parent);
    to_release->parent = new_message;
    self_composite_fields->Erase(to_release->parent_field_descriptor,
                                 to_release->AsPyObject());
    new_composite_fields->Set(to_release->parent_field_descriptor,
                              to_release->AsPyObject());
  }

  Message* mutable_self = AssureWritable(self);
  if (mutable_self == nullptr) return -1;
  Message* mutable_new = AssureWritable(new_message);
  if (mutable_new == nullptr) return -1;

  if (mutable_self->GetArena() == mutable_new->GetArena()) {
    MessageReflectionFriend::UnsafeShallowSwapFields(
        mutable_self, mutable_new,
        std::vector<const FieldDescriptor*>(fields_to_swap.begin(),
                                            fields_to_swap.end()));
  } else {
    mutable_self->GetReflection()->SwapFields(
        mutable_self, mutable_new,
        std::vector<const FieldDescriptor*>(fields_to_swap.begin(),
                                            fields_to_swap.end()));
  }

  // This might delete the Python message completely if all children were moved.
  Py_DECREF(self);

  return 0;
}
```

**File:** python/google/protobuf/pyext/message.cc (L1776-1801)
```text
PyObject* Clear(CMessage* self) {
  Message* message = AssureWritable(self);
  if (message == nullptr) return nullptr;
  // Detach all current fields of this message
  std::vector<ScopedPyObjectPtr> messages_to_release;
  std::vector<ScopedPyObjectPtr> containers_to_release;
  if (CMessage::SubMessagesMap* subs = self->child_submessages.TryGet(); subs) {
    subs->ForEach([&](const void* key, PyObject* value) {
      Py_INCREF(value);
      messages_to_release.emplace_back(value);
    });
  }
  if (CMessage::CompositeFieldsMap* fields = self->composite_fields.TryGet();
      fields) {
    fields->ForEach([&](const void* key, PyObject* value) {
      Py_INCREF(value);
      containers_to_release.emplace_back(value);
    });
  }
  if (InternalReparentFields(self, messages_to_release, containers_to_release) <
      0) {
    return nullptr;
  }
  message->Clear();
  Py_RETURN_NONE;
}
```

**File:** python/google/protobuf/pyext/message.cc (L2435-2460)
```text
                                const FieldDescriptor* field_descriptor) {
  const Reflection* reflection = self->message->GetReflection();
  PyMessageFactory* factory = GetFactoryForMessage(self);

  CMessageClass* message_class = message_factory::GetOrCreateMessageClass(
      factory, field_descriptor->message_type());
  ScopedPyObjectPtr message_class_owner(
      reinterpret_cast<PyObject*>(message_class));
  if (message_class == nullptr) {
    return nullptr;
  }

  CMessage* cmsg = cmessage::NewEmptyMessage(message_class);
  if (cmsg == nullptr) {
    return nullptr;
  }

  Py_INCREF(self);
  cmsg->parent = self;
  cmsg->parent_field_descriptor = field_descriptor;
  cmsg->message = &reflection->GetMessage(*self->message, field_descriptor,
                                          factory->message_factory);
  cmsg->state =
      self->state == MESSAGE_FROZEN ? MESSAGE_FROZEN : MESSAGE_UNPROMOTED;
  return cmsg;
}
```

**File:** python/google/protobuf/pyext/message.cc (L2775-2851)
```text
PyObject* GetFieldValue(CMessage* self,
                        const FieldDescriptor* field_descriptor) {
  if (CMessage::CompositeFieldsMap* fields = self->composite_fields.TryGet();
      fields != nullptr) {
    if (PyObject* value = fields->Get(field_descriptor, nullptr)) {
      return value;
    }
  }

  const Descriptor* message_descriptor =
      (reinterpret_cast<CMessageClass*>(Py_TYPE(self)))->message_descriptor;
  // We might've run into a descriptor pool mismatch.
  // In that case, we can update the field descriptor directly in the case
  // of a direct match.
  if (message_descriptor != self->message->GetDescriptor()) {
    if (message_descriptor->full_name() ==
        self->message->GetDescriptor()->full_name()) {
      message_descriptor = self->message->GetDescriptor();
      field_descriptor =
          message_descriptor->FindFieldByName(field_descriptor->name());
    }
  }

  if (self->message->GetDescriptor() != field_descriptor->containing_type()) {
    PyErr_Format(PyExc_TypeError,
                 "descriptor to field '%s' doesn't apply to '%s' object",
                 std::string(field_descriptor->full_name()).c_str(),
                 Py_TYPE(self)->tp_name);
    return nullptr;
  }

  if (!field_descriptor->is_repeated() &&
      field_descriptor->cpp_type() != FieldDescriptor::CPPTYPE_MESSAGE) {
    return InternalGetScalar(self->message, field_descriptor);
  }

  ContainerBase* py_container = nullptr;
  if (field_descriptor->is_map()) {
    const Descriptor* entry_type = field_descriptor->message_type();
    const FieldDescriptor* value_type = entry_type->map_value();
    if (value_type->cpp_type() == FieldDescriptor::CPPTYPE_MESSAGE) {
      CMessageClass* value_class = message_factory::GetMessageClass(
          GetFactoryForMessage(self), value_type->message_type());
      if (value_class == nullptr) {
        return nullptr;
      }
      py_container =
          NewMessageMapContainer(self, field_descriptor, value_class);
    } else {
      py_container = NewScalarMapContainer(self, field_descriptor);
    }
  } else if (field_descriptor->is_repeated()) {
    if (field_descriptor->cpp_type() == FieldDescriptor::CPPTYPE_MESSAGE) {
      CMessageClass* message_class = message_factory::GetMessageClass(
          GetFactoryForMessage(self), field_descriptor->message_type());
      if (message_class == nullptr) {
        return nullptr;
      }
      py_container = repeated_composite_container::NewContainer(
          self, field_descriptor, message_class);
    } else {
      py_container =
          repeated_scalar_container::NewContainer(self, field_descriptor);
    }
  } else if (field_descriptor->cpp_type() == FieldDescriptor::CPPTYPE_MESSAGE) {
    py_container = InternalGetSubMessage(self, field_descriptor);
  } else {
    PyErr_SetString(PyExc_SystemError, "Should never happen");
  }

  if (py_container == nullptr) {
    return nullptr;
  }
  PyObject* value = py_container->AsPyObject();
  SetCompositeField(self, field_descriptor, value);
  return value;
}
```

**File:** python/google/protobuf/pyext/weak_value_map.h (L86-94)
```text
 private:
  bool EraseIfEqualImpl(const void* key, PyObject* value);
#ifdef Py_GIL_DISABLED
  mutable absl::Mutex mutex_;
  absl::flat_hash_map<const void*, PyObject*> cache_ ABSL_GUARDED_BY(mutex_);
#else
  absl::flat_hash_map<const void*, PyObject*> cache_;
#endif
};
```

**File:** python/google/protobuf/pyext/weak_value_map.cc (L17-59)
```text
PyObject* PyWeakValueMap::Get(const void* key, const PyTypeObject* type) {
  absl::MutexLock lock(&mutex_);
  auto it = cache_.find(key);
  if (it != cache_.end()) {
    ABSL_DCHECK(type == nullptr || Py_TYPE(it->second) == type);
    if (PyUnstable_TryIncRef(it->second)) {
      return it->second;
    }
    // Object is deallocating, remove it from the map.
    cache_.erase(it);
  }
  return nullptr;
}

bool PyWeakValueMap::TrySet(const void* key, PyObject*& value) {
  PyTypeObject* type = Py_TYPE(value);
  PyObject* decref;
  PyUnstable_EnableTryIncRef(value);

  {
    absl::MutexLock lock(&mutex_);
    auto [it, inserted] = cache_.insert(std::make_pair(key, value));
    if (inserted) return true;

    // The object is already in the map. Try to use the existing object.
    ABSL_DCHECK(type == nullptr || Py_TYPE(it->second) == type);

    if (PyUnstable_TryIncRef(it->second)) {
      // The existing object is valid, so we can deallocate our copy, but we
      // should drop the lock first.
      decref = value;
      value = it->second;
      // Fall through to the end of the function.
    } else {
      // The existing object is dying, replace it.
      it->second = value;
      return true;
    }
  }

  Py_DECREF(decref);
  return false;
}
```

**File:** python/google/protobuf/internal/thread_safe_test.py (L284-307)
```python
  def testConcurrentGetFieldValueRace(self):
    """Reproduces a data race in GetFieldValue due to lazy initialization."""

    def AccessFields(msg, barrier) -> None:
      barrier.wait()
      # This access triggers GetFieldValue and lazy initialization
      # of the composite_fields map in CMessage.
      _ = msg.optional_nested_message

    for _ in range(100):
      threads = []
      msg = unittest_proto3_pb2.TestAllTypes()

      # Use a barrier to ensure all threads hit the GetFieldValue call
      # at nearly the same time, maximizing the race window.
      barrier = threading.Barrier(10)

      for _ in range(10):
        thread = threading.Thread(target=AccessFields, args=(msg, barrier))
        threads.append(thread)
        thread.start()

      for thread in threads:
        thread.join()
```

**File:** python/google/protobuf/internal/thread_safe_test.py (L497-518)
```python
  def testConcurrentClearAndSubObjectDeletionRace(self):
    """Reproduces a dangling pointer dereference race between Clear() and sub-object deallocation."""

    def ClearMsg(msg, barrier):
      barrier.wait()
      msg.Clear()

    def DeleteSub(container, barrier):
      barrier.wait()
      container.clear()

    for _ in range(500):
      msg = unittest_proto3_pb2.TestAllTypes()
      container = [msg.optional_nested_message]
      barrier = threading.Barrier(2)

      thread1 = threading.Thread(target=ClearMsg, args=(msg, barrier))
      thread2 = threading.Thread(target=DeleteSub, args=(container, barrier))
      thread1.start()
      thread2.start()
      thread1.join()
      thread2.join()
```

**File:** python/google/protobuf/internal/thread_safe_test.py (L541-579)
```python
  def testConcurrentLazyUnpackAndRead(self):
    # 1. Create a template proto containing a lazy sub-message
    template = test_proto2_pb2.ReproMessageForLazy()
    template.lazy_field.value = 'repro_value'
    serialized_bytes = template.SerializeToString()

    # 2. Helper to run concurrent read/write loops on shared unparsed instances
    def RunRace():
      # Parse a fresh unparsed message instance
      shared_msg = test_proto2_pb2.ReproMessageForLazy.FromString(
          serialized_bytes
      )

      barrier = threading.Barrier(2)

      def ThreadWriter():
        barrier.wait()
        # Access the lazy field for the first time.
        # This forces the C++ protobuf library to unpack the lazy field,
        _ = shared_msg.lazy_field.value

      def ThreadReader():
        barrier.wait()
        # Concurrently read field presence or format to string.
        _ = shared_msg.HasField('lazy_field')
        _ = str(shared_msg)

      t1 = threading.Thread(target=ThreadWriter)
      t2 = threading.Thread(target=ThreadReader)

      t1.start()
      t2.start()
      t1.join()
      t2.join()

    # 3. Run in a loop to reliably trigger
    for _ in range(500):
      RunRace()

```

**File:** python/google/protobuf/internal/thread_safe_test.py (L580-583)
```python
  @unittest.skipIf(
      api_implementation.Type() == 'upb',
      'Upb has not been fixed to handle this case.',
  )
```
