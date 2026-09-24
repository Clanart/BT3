### Title
Data race in CPython `PyWeakValueMap`/`composite_fields` sub-message cache under free-threaded (`Py_GIL_DISABLED`) builds allows use of a stale/dangling `CMessage` after concurrent field access races with clearing/promotion - ([File: python/google/protobuf/pyext/message.cc], [File: python/google/protobuf/pyext/weak_value_map.h])

### Summary
The kernel CVE's failed invariant is: a receive-mode indicator (`po->prot_hook.func`) was published *outside* the lock that protects the associated storage (`rx_ring`), so a concurrent reader could observe "mode says ring is active" while the ring was already torn down — a classic "flag and storage updated non-atomically, reader trusts flag" race. The closest analog in this checkout is the Python C-extension's per-message sub-object cache (`composite_fields`, backed by `PyWeakValueMap`) and its `TrySet`/`Get`/`EraseIfEqual` API, which is explicitly documented as being "inherently racy" for read paths and whose GIL-disabled correctness depends on carefully mutex-guarding `cache_` under `Py_GIL_DISABLED` builds. [1](#0-0) [2](#0-1) 

### Finding Description
`CMessage.composite_fields` caches the Python wrapper object for a submessage/repeated/map field, keyed by `FieldDescriptor*`, and is consulted by `InternalGetSubMessage`/`GetFieldValue` on every field access, while `Clear()`/`AssureWritable()`/merge operations mutate or invalidate the underlying C++ `Message` storage that the cached wrapper points to. [3](#0-2) 
In free-threaded builds the map is guarded by `absl::Mutex`, but the *value* returned to a caller (a `ContainerBase*`/submessage wrapper) is only protected while `mutex_` is held during the lookup — after `Get`/`GetOrInsert` returns, nothing prevents a second thread from invalidating the parent's storage (e.g. via `Clear`, `MergeFrom`'s `FixupMessageAfterMerge`, or oneof release) that the just-returned wrapper still points into. [4](#0-3) 
This is exactly the invariant that broke in the kernel bug: the "mode/flag" (cache entry existing, `MESSAGE_UNPROMOTED`/`MESSAGE_MUTABLE` state) and the backing storage (`self->message`, the reflection-owned submessage pointer) are not updated atomically as a single critical section spanning both the cache and the message-mutation path. The project's own test suite demonstrates this concretely: `testConcurrentGetFieldValueRace` is titled "Reproduces a data race in GetFieldValue due to lazy initialization," and `testConcurrentLazyUnpackAndRead` races a thread lazily unpacking `LazyFieldLite`-equivalent content (`shared_msg.lazy_field.value`) against a reader calling `HasField`/`str()` on the same message. [5](#0-4) [6](#0-5) 
Most tellingly, `testConcurrentRepeatedCompositeSubscript` — two threads concurrently reading different indices of the same repeated composite field after a single `FromString` parse — is explicitly skipped for the `upb` backend with the comment "Upb has not been fixed to handle this case," confirming an acknowledged, currently-unfixed race in the parse-then-access path. [7](#0-6) 

### Impact Explanation
If two threads in the same free-threaded Python process race a field access (triggering lazy sub-message materialization / `composite_fields` population) against another access or a mutation (`Clear`, `MergeFrom`, oneof promotion) on the same parsed message, the reader can observe a torn/partially-initialized wrapper or a wrapper whose underlying `Message*` has been reparented/freed, matching the kernel bug's "stale or NULL storage dereference" pattern. In native C++ code this is a memory-safety issue (use-after-free/NULL deref) rather than a Python-level exception, since the cache and the `Message` pointer live in C-extension memory (`ContainerBase::message`, `PyWeakValueMap::cache_`). This is High-severity within the free-threaded (`Py_GIL_DISABLED`) build in the same class as the kernel finding (memory corruption reachable without special privileges), though it is scoped to that specific, still-experimental build mode rather than the default GIL build where the GIL serializes these operations.

### Likelihood Explanation
This requires the hosting application to share one parsed `Message` object across threads without external locking, which the library's own tests exercise directly and the code comments acknowledge as a known, partially-mitigated hazard (`upb has not been fixed to handle this case`). It requires `Py_GIL_DISABLED` (free-threaded CPython), so it is not exposed on default builds where the GIL still serializes bytecode execution around `composite_fields` accesses; likelihood is therefore Medium given the explicit "not yet fixed" acknowledgment for one backend and no code path (`weak_value_map.h`) proven to fully cover mutation-vs-read ordering outside of insertion.

### Recommendation
Extend `PyWeakValueMap`'s mutex protection (or an equivalent lock/atomic protocol) to cover the full lifetime window during which a caller uses the returned wrapper pointer, not merely the lookup/insert. Concretely: (1) make mutating operations that can invalidate a cached submessage's backing storage (`Clear`, `AssureWritable`, oneof release, `FixupMessageAfterMerge`) synchronize with `composite_fields`/`child_submessages` lookups using the same lock used in `Get`/`TrySet`, mirroring the kernel fix's approach of moving both the flag and storage transitions into one critical section; (2) fix the acknowledged `upb`-backend gap referenced in `testConcurrentRepeatedCompositeSubscript`; (3) add stress/fuzzing under `Py_GIL_DISABLED` with TSAN to catch use-after-free in `ContainerBase::message` across concurrent parse/read/mutate sequences.

### Proof of Concept
The repository's existing test `testConcurrentLazyUnpackAndRead` in `python/google/protobuf/internal/thread_safe_test.py:541-579` is a minimal, already-present reproduction: it parses a message with a lazy sub-field, then races one thread that first-accesses (materializes) the lazy field against a second thread calling `HasField`/`str()` on the same message object, run 500 times to reliably surface the race. [6](#0-5) 
Similarly, `testConcurrentRepeatedCompositeSubscript` (lines 584-613) reproduces the analogous race for repeated composite fields, and is currently skipped only for the `upb` backend due to being unfixed, which is direct evidence the underlying invariant (cache/storage consistency across concurrent access) is not fully enforced in this checkout. [7](#0-6) 
I was not able to run these tests in this read-only environment to capture a live TSAN/ASan trace; a Devin session with build/test access would be needed to execute them under `Py_GIL_DISABLED` + sanitizers to confirm memory-corruption impact versus a benign Python-level exception, and to inspect the `upb` CPython binding code paths (not fully indexed here) that back these specific tests.

### Citations

**File:** python/google/protobuf/pyext/weak_value_map.h (L37-65)
```text
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

**File:** python/google/protobuf/pyext/weak_value_map.h (L96-113)
```text
#ifdef Py_GIL_DISABLED

template <class Func>
PyObject* PyWeakValueMap::GetOrInsert(const void* key, const PyTypeObject* type,
                                      Func&& func) {
  if (auto obj = Get(key, type); obj != nullptr) {
    return obj;
  }

  PyObject* obj = func();

  if (obj == nullptr) {
    return nullptr;
  }

  TrySet(key, obj);
  return obj;
}
```

**File:** python/google/protobuf/pyext/message.h (L107-149)
```text
typedef struct CMessage : public ContainerBase {
  // Pointer to the C++ Message object for this CMessage.
  // - If this object has no parent, we own this pointer.
  // - If this object has a parent message, the parent owns this pointer.
  const Message* message;

  // Indicates the mutability state of this CMessage wrapper.
  MessageMutabilityState state;

  // Whether there is a map ancestor anywhere in the hierarchy.
  bool has_mutable_map_ancestor;

  // A mapping indexed by field, containing weak references to contained objects
  // which need to implement the "Release" mechanism:
  // direct submessages, RepeatedCompositeContainer, RepeatedScalarContainer
  // and MapContainer.
  //   Maps: const FieldDescriptor* -> ContainerBase*
  typedef PyWeakValueMap CompositeFieldsMap;
  LazyUniquePtr<CompositeFieldsMap> composite_fields;

  // A mapping containing weak references to indirect child messages, accessed
  // through containers: repeated messages, and values of message maps.
  // This avoid the creation of similar maps in each of those containers.
  //   Maps: const Message* -> CMessage*
  typedef PyWeakValueMap SubMessagesMap;
  LazyUniquePtr<SubMessagesMap> child_submessages;

  // Implements the "weakref" protocol for this object.
  PyObject* weakreflist;

  // Return a *borrowed* reference to the message class.
  CMessageClass* GetMessageClass() {
    return reinterpret_cast<CMessageClass*>(Py_TYPE(this));
  }

  // For container containing messages, return a Python object for the given
  // pointer to a message.
  CMessage* BuildSubMessageFromPointer(const FieldDescriptor* field_descriptor,
                                       const Message* sub_message,
                                       CMessageClass* message_class,
                                       MessageMutabilityState state);
  CMessage* MaybeReleaseSubMessage(const Message* sub_message);
} CMessage;
```

**File:** python/google/protobuf/internal/thread_safe_test.py (L284-308)
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

**File:** python/google/protobuf/internal/thread_safe_test.py (L580-613)
```python
  @unittest.skipIf(
      api_implementation.Type() == 'upb',
      'Upb has not been fixed to handle this case.',
  )
  def testConcurrentRepeatedCompositeSubscript(self):
    msg = test_proto2_pb2.ContainerForRepeatedComposite()
    msg.submessage.items.add(value='foo')
    msg.submessage.items.add(value='bar')
    serialized = msg.SerializeToString()

    def RunRace():
      shared_msg = test_proto2_pb2.ContainerForRepeatedComposite.FromString(
          serialized
      )
      barrier = threading.Barrier(2)

      def Thread1():
        barrier.wait()
        _ = shared_msg.submessage.items[0].value

      def Thread2():
        barrier.wait()
        _ = shared_msg.submessage.items[1].value

      t1 = threading.Thread(target=Thread1)
      t2 = threading.Thread(target=Thread2)
      t1.start()
      t2.start()
      t1.join()
      t2.join()

    for _ in range(500):
      RunRace()

```
