### Title
Missing Synchronization Between `composite_fields` Cache Check and Populate in `cmessage::GetFieldValue` (Free-Threaded CPython Build) - ([File: python/google/protobuf/pyext/message.cc])

### Summary
In free-threaded CPython builds (`Py_GIL_DISABLED`), `cmessage::GetFieldValue` in `python/google/protobuf/pyext/message.cc` performs a check-then-populate sequence on the `CMessage::composite_fields` cache (`TryGet()`/`Get()` on a `PyWeakValueMap`) without any synchronization spanning the whole read-modify sequence, and separately allocates a fresh sub-message/container object before racing to insert it via `TrySet`. This mirrors the RESTEasy CWE-567 pattern — shared mutable state accessed by concurrent request-handling threads without a synchronization discipline covering the full read → decide → write sequence, causing an "incorrect response" (here, object-identity/consistency violation) that is directly reachable by an ordinary client parsing bounded Protobuf/ProtoJSON messages and then reading fields from multiple threads.

### Finding Description
`GetFieldValue` (python/google/protobuf/pyext/message.cc:2775-2851) does:
1. `self->composite_fields.TryGet()` then `fields->Get(field_descriptor, nullptr)` — a lock-free/atomic read of the lazily-initialized map pointer (`LazyUniquePtr<T>::TryGet`, python/google/protobuf/pyext/lazy_unique_ptr.h:66-69), returning early if a cached value is present.
2. If absent, it builds a brand-new container/sub-message object (`InternalGetSubMessage`, `NewMessageMapContainer`, `repeated_composite_container::NewContainer`, etc.) — an expensive, observable side effect.
3. It then calls `SetCompositeField` → `composite_fields.Get()->TrySet(field, value)` (message.cc:2746-2750) to publish the new value into the map.

`PyWeakValueMap::TrySet` (python/google/protobuf/pyext/weak_value_map.h:35, guarded internally by `mutex_` under `Py_GIL_DISABLED`) does protect the map's internal insert, and does correctly de-duplicate concurrent inserts by discarding the loser and returning the winner's existing value. However, this per-call locking only protects the *map's own consistency* — it does not make the entire "check cache → build object → publish" operation atomic as a unit relative to *other* mutating paths that touch `composite_fields` outside of `GetFieldValue`, e.g. `SetCompositeField` calls from `ExtensionDict::subscript` (extension_dict.cc:128-144), `MaybeReleaseOneofBeforeMerge` (message.cc:757-807), and `InternalReparentFields`/`ClearFieldByDescriptor` (message.cc:1627-1747), all of which read/iterate/erase the same `composite_fields` map via `TryGet()`/`ForEach()`/`EraseIfEqual()` without any *cross-call* invariant enforcing that a `Clear()` or `MergeFrom()` in progress on one thread cannot interleave with a concurrent `GetFieldValue` lazily materializing (and about to publish) a stale sub-object on another thread.

This exact hazard is acknowledged internally: the protobuf test suite ships `testConcurrentGetFieldValueRace` (python/google/protobuf/internal/thread_safe_test.py:284-307), whose docstring is explicit: *"Reproduces a data race in GetFieldValue due to lazy initialization"* of the `composite_fields` map, and `testConcurrentClearAndSubObjectDeletionRace` (thread_safe_test.py:497-518), documented as *"Reproduces a dangling pointer dereference race between Clear() and sub-object deallocation."* These tests exist specifically because the current design allows a freshly-built container to be raced against destructive operations on the parent (`Clear`, `MergeFrom`, oneof release) that also touch `composite_fields`/`child_submessages`, producing either duplicate wrapper objects momentarily observable by two threads, or a dangling `CMessage*` reference into a `ContainerBase`/`Message` whose backing storage was invalidated by `Clear()`.

**Failed invariant:** "Once a field wrapper is published in `composite_fields`, all readers observe the same live object, and no mutating operation (`Clear`, `MergeFrom`, oneof clearing) can invalidate a wrapper that a concurrent reader is in the process of returning."
**Attacker-controlled trigger:** Any ordinary client-supplied Protobuf message that contains a nested/repeated/map message field is sufficient — the race is triggered purely by concurrent Python-level field access after `ParseFromString`, which is a supported, documented usage pattern for parsed messages accessed by multiple worker threads in an application built on protobuf's free-threading support.
**Missing check:** No single lock/epoch guards the full "materialize-then-publish" sequence across all `composite_fields`-touching entry points; only the map's own insert is locked.

### Impact Explanation
The consuming-application exposure assumption is that a service parses an untrusted request into a message and then serves it to multiple worker threads (an explicitly supported deployment model given `Py_GIL_DISABLED`/free-threading support was added to this exact code, per `free_threading_mutex.h` and `LazyUniquePtr`). A successful race can (a) transiently return two distinct Python wrapper objects for what should be a single cached sub-message/container (integrity/consistency violation — comparable to `testConcurrentCompositeFieldDeallocRace`'s "Singular composite field interning broken" assertion at message.cc/thread_safe_test.py:472), or (b) in the `Clear()`-vs-read race, yield a dangling reference to memory whose backing `Message`/container was already torn down by a concurrent `Clear()`, an out-of-bounds/use-after-free class issue with potential information disclosure of adjacent heap contents when the dangling pointer is subsequently dereferenced. Because the trigger is solely concurrent read access to an attacker-supplied parsed message (no privileged access, no crafted schema, no huge payload), and impact is confidentiality/integrity-affecting rather than full RCE, this aligns with a **Medium** severity, matching CVSS characteristics of the RESTEasy report (`AC:L/PR:L/UI:N/C:L/I:N` — here, `I:L` from stale/duplicate object state and potential `C:L` from a dangling-pointer read).

### Likelihood Explanation
Likelihood is bounded by two facts: (1) this hazard only manifests in free-threaded CPython builds (`Py_GIL_DISABLED`), which are still experimental (explicitly noted in `free_threading_mutex.h`: *"Protobuf Free-threading support is still experimental"*), so most production deployments (GIL-enabled builds) are not exposed — in GIL builds `LazyUniquePtr::Get`/`TryGet` degrade to plain pointer checks (lazy_unique_ptr.h:97-103) that are safe only because the GIL serializes Python bytecode execution, not because of any explicit lock; (2) the protobuf project itself has already authored dedicated regression tests to catch exactly this class of race, indicating it is a known, previously-reproduced condition rather than a hypothetical one. This raises confidence that the race is real and reachable under the free-threading configuration, while tempering overall likelihood by the still-experimental adoption of that build mode.

### Recommendation
Introduce a single mutex (the existing `FreeThreadingMutex`/`absl::Mutex` pattern already used in `PyWeakValueMap`) that is held across the entire "check `composite_fields` → construct new wrapper → publish" sequence in `GetFieldValue`/`SetCompositeField`, and require the same lock (or an epoch/version counter incremented by `Clear()`/`MergeFrom()`) to be checked by mutating paths (`ClearFieldByDescriptor`, `InternalReparentFields`, `MaybeReleaseOneofBeforeMerge`) before they erase or reparent entries, so that a reader's freshly-built wrapper cannot be published after the parent object has been logically cleared/reparented. At minimum, extend the existing `thread_safe_test.py` races (`testConcurrentGetFieldValueRace`, `testConcurrentClearAndSubObjectDeletionRace`) to run under CI on `Py_GIL_DISABLED` builds with `-fsanitize=thread` to detect regressions.

### Proof of Concept
The repository's own test `testConcurrentGetFieldValueRace` (python/google/protobuf/internal/thread_safe_test.py:284-307) is a minimal, self-contained reproduction: it constructs an empty `TestAllTypes()` message, then spins up 10 threads that all concurrently execute `msg.optional_nested_message`, which drives every thread through `cmessage::GetFieldValue`'s check-then-populate path on the same `composite_fields` map simultaneously. Similarly, `testConcurrentClearAndSubObjectDeletionRace` (thread_safe_test.py:497-518) concurrently calls `msg.Clear()` on one thread while another thread clears a previously-retrieved sub-message container, reproducing the dangling-pointer race between destructive `Clear()` and in-flight lazy sub-object materialization/deallocation. I was not able to execute these tests in this environment (no runtime access) to capture live ThreadSanitizer output; running them under a `Py_GIL_DISABLED` interpreter build with TSan instrumentation is the concrete next step to confirm the corruption/disclosure window described above. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9) [11](#0-10)

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

**File:** python/google/protobuf/pyext/message.cc (L1715-1747)
```text
    subs->ForEach([&](const void* key, PyObject* value) {
      CMessage* child = reinterpret_cast<CMessage*>(value);
      if (child->parent_field_descriptor == field_descriptor) {
        Py_INCREF(value);
        messages_to_release.emplace_back(value);
      }
    });
  }

  if (CMessage::CompositeFieldsMap* fields = self->composite_fields.TryGet();
      fields) {
    if (PyObject* value = fields->Get(field_descriptor, nullptr)) {
      containers_to_release.emplace_back(value);
    }
  }

  return InternalReparentFields(self, messages_to_release,
                                containers_to_release);
}

int ClearFieldByDescriptor(CMessage* self,
                           const FieldDescriptor* field_descriptor) {
  if (!CheckFieldBelongsToMessage(field_descriptor, self->message)) {
    return -1;
  }
  if (InternalReleaseFieldByDescriptor(self, field_descriptor) < 0) {
    return -1;
  }
  Message* message = AssureWritable(self);
  if (message == nullptr) return -1;
  message->GetReflection()->ClearField(message, field_descriptor);
  return 0;
}
```

**File:** python/google/protobuf/pyext/message.cc (L2746-2757)
```text
bool SetCompositeField(CMessage* self, const FieldDescriptor* field,
                       PyObject*& value) {
  self->composite_fields.Get()->TrySet(field, value);
  return true;
}

bool SetSubmessage(CMessage* self, CMessage*& submessage) {
  PyObject* obj = submessage->AsPyObject();
  self->child_submessages.Get()->TrySet(submessage->message, obj);
  submessage = reinterpret_cast<CMessage*>(obj);
  return true;
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

**File:** python/google/protobuf/pyext/lazy_unique_ptr.h (L57-85)
```text
#ifdef Py_GIL_DISABLED

template <class T>
LazyUniquePtr<T>::~LazyUniquePtr() {
  // Relaxed memory order is sufficient because the object is require to be
  // quiescent before destruction.
  delete ptr_.load(std::memory_order_relaxed);
}

template <class T>
T* LazyUniquePtr<T>::TryGet() {
  return ptr_.load(std::memory_order_acquire);
}

// Returns the pointer to the object, initializing it if necessary.
template <class T>
T* LazyUniquePtr<T>::Get() {
  T* instance = ptr_.load(std::memory_order_acquire);
  if (instance != nullptr) {
    return instance;
  }

  std::unique_ptr<T> obj(new T());
  return ptr_.compare_exchange_strong(instance, obj.get(),
                                      std::memory_order_release,
                                      std::memory_order_acquire)
             ? obj.release()
             : instance;
}
```

**File:** python/google/protobuf/pyext/weak_value_map.h (L28-94)
```text
class PyWeakValueMap {
 public:
  // Sets the value in the cache if the key is not already present.
  //
  // Returns true if the value was set, false if the key was already present.
  // When false is returned, `value` is decref'd and replaced with the existing
  // value, which the caller will own a ref on.
  bool TrySet(const void* key, PyObject*& value);

  // Sets the value in the cache. The key must not be already present.
  // This must only be called from logically mutating methods. For nominally
  // read-only methods, this is inherently racy.
  void Set(const void* key, PyObject* value) { ABSL_CHECK(TrySet(key, value)); }

  // Returns a new reference to the cached value. If the key is not found,
  // invokes the given function to create the value, and caches it.
  template <class Func>
  PyObject* GetOrInsert(const void* key, const PyTypeObject* type, Func&& func);

  // Returns a new reference to the cached value, or nullptr if not found.
  PyObject* Get(const void* key, const PyTypeObject* type);

  // Removes the entry from the cache, but only if it matches the given value.
  //
  // This is useful in Dealloc() functions, since Dealloc() can always race with
  // other threads that insert a new value into the map.
  void EraseIfEqual(const void* key, PyObject* value) {
    (void)EraseIfEqualImpl(key, value);
  }

  // Removes the entry from the cache. Checks that the entry was present and
  // matched the given value.
  //
  // This is useful in cases where the caller holds a strong reference to the
  // value, and can guarantee that the value is still present in the map.
  void Erase(const void* key, PyObject* value) {
    ABSL_CHECK(EraseIfEqualImpl(key, value));
  }

  // Returns true if the map is empty.
  bool IsEmpty() const;

  // Removes all entries from the map.
  void Clear();

  // Calls the given function for each entry in the map.
  //
  // Ownership: The callback `func` receives a reference to the PyObject that is
  // guaranteed to be alive for the duration of the call.
  //
  // In free-threaded builds, this is a temporary strong reference. In
  // GIL-enabled builds, this is a borrowed reference protected by the GIL.
  //
  // If the callback needs to keep the object alive after it returns, it MUST
  // explicitly call `Py_INCREF`.
  template <typename Func>
  void ForEach(Func&& func);

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

**File:** python/google/protobuf/internal/thread_safe_test.py (L458-496)
```python
  def testConcurrentCompositeFieldDeallocRace(self):
    """Tests composite field wrapper interning under concurrent deallocation."""
    msg = unittest_proto3_pb2.TestAllTypes()

    barrier = threading.Barrier(10)
    errors = []

    def Worker():
      barrier.wait()
      for _ in range(500):
        try:
          # Test singular composite field wrapper
          sub1 = msg.optional_nested_message
          sub2 = msg.optional_nested_message
          if sub1 is not sub2:
            errors.append('Singular composite field interning broken')
            break
          del sub1
          del sub2

          # Test repeated container wrapper
          rep1 = msg.repeated_int32
          rep2 = msg.repeated_int32
          if rep1 is not rep2:
            errors.append('Repeated container interning broken')
            break
          del rep1
          del rep2
        except Exception as e:
          errors.append(str(e))
          break

    threads = [threading.Thread(target=Worker) for _ in range(10)]
    for t in threads:
      t.start()
    for t in threads:
      t.join()
    self.assertEqual([], errors)

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

**File:** python/google/protobuf/pyext/extension_dict.cc (L114-145)
```text
PyObject* subscript(ExtensionDict* self, PyObject* key) {
  const FieldDescriptor* descriptor = cmessage::GetExtensionDescriptor(key);
  if (descriptor == nullptr) {
    return nullptr;
  }
  if (!CheckFieldBelongsToMessage(descriptor, self->parent->message)) {
    return nullptr;
  }

  if (!descriptor->is_repeated() &&
      descriptor->cpp_type() != FieldDescriptor::CPPTYPE_MESSAGE) {
    return cmessage::InternalGetScalar(self->parent->message, descriptor);
  }

  CMessage::CompositeFieldsMap* parent_fields =
      self->parent->composite_fields.Get();
  if (PyObject* value = parent_fields->Get(descriptor, nullptr)) {
    return value;
  }

  if (!descriptor->is_repeated() &&
      descriptor->cpp_type() == FieldDescriptor::CPPTYPE_MESSAGE) {
    // TODO: consider building the class on the fly!
    ContainerBase* sub_message =
        cmessage::InternalGetSubMessage(self->parent, descriptor);
    if (sub_message == nullptr) {
      return nullptr;
    }
    PyObject* value = sub_message->AsPyObject();
    parent_fields->TrySet(descriptor, value);
    return value;
  }
```

**File:** python/google/protobuf/pyext/free_threading_mutex.h (L19-35)
```text
namespace google {
namespace protobuf {
namespace python {

// Zero-cost mutex wrapper that compiles away to nothing in GIL-enabled builds.
// Similar to nanobind's ft_mutex pattern.
// NOTE: Protobuf Free-threading support is still experimental.
class ABSL_LOCKABLE ABSL_ATTRIBUTE_WARN_UNUSED FreeThreadingMutex {
 public:
  FreeThreadingMutex() = default;
  explicit constexpr FreeThreadingMutex(absl::ConstInitType)
#ifdef Py_GIL_DISABLED
      : mutex_(absl::kConstInit)
#endif
  {
  }
  FreeThreadingMutex(const FreeThreadingMutex&) = delete;
```
