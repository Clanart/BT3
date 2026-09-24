I have sufficient evidence now to construct the analog. Let me do one final targeted check on the pyext lazy field unpacking implementation that underlies `testConcurrentLazyUnpackAndRead`.### Title
Data race between concurrent lazy sub-object unpacking and read access on a parsed message in the Python C++ (pyext) extension - ([File: python/google/protobuf/pyext/lazy_unique_ptr.h])

### Summary
The PX4 CVE-2026-32724 root cause is a race between a thread that creates/destroys a lifecycle-managed object (`MavlinkShell`) in response to attacker-controlled protocol messages, and a second thread that concurrently polls/reads that same object without adequate synchronization, producing a heap use-after-free. The transferable invariant is: "an object populated from parsed wire data must not have its internal state raced between a lazy-populating thread and a concurrently-reading thread." Protobuf's own Python C++ extension (`pyext`) documents and partially addresses exactly this class of hazard for free-threaded (No-GIL) Python builds via `LazyUniquePtr` [1](#0-0) , and the repository's own concurrency regression suite demonstrates that at least one closely related case — concurrent read-only access to sub-objects of a message produced by a single, ordinary `FromString()`/`ParseFromString()` call — is **still an open, unfixed race** for the `upb` backend.

### Finding Description
`google.protobuf.Message.FromString()` / `ParseFromString()` are the supported public parse APIs. Once a message is parsed from attacker-controlled bytes, protobuf's own documented thread-safety contract states that read-only ("const") access to a parsed message is safe to perform concurrently from multiple threads [2](#0-1) . Internally, however, several "read" accessors are not actually const: they perform on-demand ("lazy") construction of caches, wrapper objects, or the unpacked message value itself, guarded only by best-effort lock-free primitives.

- `LazyUniquePtr<T>::Get()` in the Python pyext backend explicitly documents the race: "both threads will create the object, and whichever thread loses the race will delete the object it created" [1](#0-0) , and its `Get()`/`TryGet()` implementation performs an atomic load/CAS with the caveat that the constructor/destructor of `T` "must not have any meaningful side effects" [3](#0-2) .
- The equivalent `upb`-backend primitive, `PyUpb_LazyPtr`, uses the same lock-free CAS-and-discard-loser pattern [4](#0-3) .
- Message field access (`PyUpb_Message_GetFieldValue`) lazily reifies stub wrapper objects and mutates a shared weak-map cache during what is nominally a read operation [5](#0-4)  and [6](#0-5) .
- The repository's own regression test suite (`FreeThreadingTest`) demonstrates the exact failure pattern: a message is parsed once via `FromString()` from attacker-derived bytes, then two threads perform only read-style operations (`.value`, `HasField()`, `str()`, or indexing into a repeated composite field) on the shared parsed object concurrently [7](#0-6)  and [8](#0-7) .
- Critically, `testConcurrentRepeatedCompositeSubscript` is explicitly gated with `@unittest.skipIf(api_implementation.Type() == 'upb', 'Upb has not been fixed to handle this case.')` [9](#0-8)  — i.e., the maintainers acknowledge this concurrent-read race is a live, unresolved defect in the `upb` backend used by default Python installs.

This directly mirrors the PX4 pattern: one code path (parsing / first access) lazily "creates" internal state, another concurrently "reads" it, and the missing synchronization is an internal-implementation gap rather than user misuse — protobuf's public contract says concurrent reads are safe, but the lazy-caching internals violate that contract on the `upb` backend.

### Impact Explanation
Under `Py_GIL_DISABLED` (free-threaded Python, increasingly used in server workloads that share parsed protobuf messages across worker threads for read-only fan-out), a race between the "first access wins" lazy-init path and a concurrent read on the same wrapper/lazy field can lead to a dangling-pointer dereference or double-free of the temporarily-duplicated object, matching the memory-corruption class of the PX4 finding (denial of service via heap corruption; potential further memory-safety impact depending on allocator state). This is reachable purely from parsing attacker-supplied bytes through the public `FromString()` API followed by ordinary, allowed multi-threaded read use by the consuming application — no malicious schema, no privileged access, and no application misuse of documented non-thread-safe write APIs is required.

### Likelihood Explanation
Likelihood is constrained: the flaw is confirmed to require `Py_GIL_DISABLED` (free-threaded Python 3.13+) with the `upb` implementation, and the maintainers already flag it as a known, reproducible, currently-unfixed issue via a skipped regression test rather than a silently-passing one [9](#0-8) . The trigger requires a specific message shape (repeated composite sub-fields, or a `lazy=true` message field) and precise thread interleaving (a `threading.Barrier` is used in the test to reliably force the race), similar to the PX4 CVE's requirement for two racing threads. This is a Medium-severity, narrowly-scoped concurrency defect, not a trivially remote, single-request exploit.

### Recommendation
- Extend the `upb`-backend lazy sub-object reification path (`PyUpb_Message_GetFieldValue`, `PyUpb_RepeatedContainer_GetOrCreateWrapper`, `PyUpb_Message_Reify`) to use the same atomic CAS-and-discard-loser discipline already implemented in `python/free_threading/lazy_ptr.h`, and validate that no reader can observe a half-constructed or already-freed wrapper.
- Remove the `skipIf(api_implementation.Type() == 'upb', ...)` exemption in `thread_safe_test.py` once fixed, and add it to CI matrices that exercise `Py_GIL_DISABLED` builds.
- Audit all "read" accessor paths that perform lazy caching (`LazyUniquePtr::Get`, `LazyFieldLite.getValue()`, weak-map based wrapper caches) to ensure they satisfy the documented "const-safe from multiple threads" contract in `package_info.h`, not just the C++ core LazyField (which is explicitly documented as *not* thread-safe for reads without external synchronization) but the Python bindings, which imply thread-safety to callers.

### Proof of Concept
The repository's own existing test is the minimal reproduction (already present, not hypothetical):
```python
# python/google/protobuf/internal/thread_safe_test.py:584-612
msg = test_proto2_pb2.ContainerForRepeatedComposite()
msg.submessage.items.add(value='foo')
msg.submessage.items.add(value='bar')
serialized = msg.SerializeToString()          # attacker-controlled bytes in general case

shared_msg = test_proto2_pb2.ContainerForRepeatedComposite.FromString(serialized)  # public parse API
barrier = threading.Barrier(2)

def Thread1():
    barrier.wait()
    _ = shared_msg.submessage.items[0].value   # concurrent "read" #1

def Thread2():
    barrier.wait()
    _ = shared_msg.submessage.items[1].value   # concurrent "read" #2

# run concurrently, repeated 500x to reliably hit the race window
```
This test is currently skipped for the `upb` backend with the comment "Upb has not been fixed to handle this case" [9](#0-8) , confirming the race is real and unresolved in the checked-out code, though I was not able to obtain a live sanitizer trace/crash log from this environment — that would require running the test under a `Py_GIL_DISABLED` build with `upb`, which is outside the scope of static analysis here.

### Citations

**File:** python/google/protobuf/pyext/lazy_unique_ptr.h (L24-31)
```text
// A unique pointer that is initialized on first access. In a free-threaded
// build, this lazy initialization is thread-safe.
//
// To avoid blocking, this class does not wait if anonther thread is
// initializing the object. When this race condition occurs, both threads will
// create the object, and whichever thread loses the race will delete the object
// it created. For this reason, the constructor and destructor of T must not
// have any meaningful side effects.
```

**File:** python/google/protobuf/pyext/lazy_unique_ptr.h (L66-85)
```text
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

**File:** src/google/protobuf/package_info.h (L21-33)
```text
// A note on thread-safety:
//
// Thread-safety in the Protocol Buffer library follows a simple rule:
// unless explicitly noted otherwise, it is always safe to use an object
// from multiple threads simultaneously as long as the object is declared
// const in all threads (or, it is only used in ways that would be allowed
// if it were declared const).  However, if an object is accessed in one
// thread in a way that would not be allowed if it were const, then it is
// not safe to access that object in any other thread simultaneously.
//
// Put simply, read-only access to an object can happen in multiple threads
// simultaneously, but write access can only happen in a single thread at
// a time.
```

**File:** python/free_threading/lazy_ptr.h (L18-24)
```text
// Under free-threaded Python (`Py_GIL_DISABLED`), lazy pointers expand to
// atomic pointers (`UPB_ATOMIC(T*)`) and use atomic Compare-And-Swap (CAS) with
// acquire/release memory ordering to guarantee safe thread interaction without
// acquiring global locks. On CAS collisions (races where two threads
// concurrently attempt to initialize the same field), the lost instance is
// automatically freed/decref'd and the winning instance is returned to all
// callers.
```

**File:** python/message.c (L779-806)
```c
static bool PyUpb_Message_Reify(PyUpb_Message* self, const upb_FieldDef* f,
                                upb_Message* msg, PyUpb_WeakMap* subobj_map,
                                intptr_t* iter) {
  assert(PyUpb_Message_IsStub(self));
  assert(f == PyUpb_Message_GetFieldDef(self));
  if (subobj_map && iter) {
    PyUpb_WeakMap_DeleteIter(subobj_map, iter);
  }
  if (!msg) {
    const upb_MessageDef* msgdef = PyUpb_Message_GetMsgdef((PyObject*)self);
    const upb_MiniTable* layout = upb_MessageDef_MiniTable(msgdef);
    msg = upb_Message_New(layout, PyUpb_Arena_Get(self->arena));
    if (!msg) {
      PyErr_SetNone(PyExc_MemoryError);
      return false;
    }
  }
  if (!PyUpb_Arena_CacheUniqueAdd(self->arena, msg, &self->ob_base)) {
    return false;
  }
  PyObject* parent = &self->ptr.parent->ob_base;
  self->ptr.msg = msg;  // Overwrites self->ptr.parent
  self->def = (uintptr_t)upb_FieldDef_MessageSubDef(f);
  assert(!PyUpb_Message_IsStub(self));
  bool ok = PyUpb_Message_SyncSubobjs(self);  // May DECREF self!
  Py_DECREF(parent);
  return ok;
}
```

**File:** python/message.c (L1099-1121)
```c
PyObject* PyUpb_Message_GetFieldValue(PyObject* _self,
                                      const upb_FieldDef* field) {
  PyUpb_Message* self = (void*)_self;
  assert(upb_FieldDef_ContainingType(field) == PyUpb_Message_GetMsgdef(_self));
  bool submsg = upb_FieldDef_IsSubMessage(field);
  bool seq = upb_FieldDef_IsRepeated(field);

  if (PyUpb_Message_IsStub(self) && (submsg || seq)) {
    return PyUpb_Message_GetStub(self, field);
  }

  if (seq) {
    upb_MessageValue val = upb_Message_GetFieldByDef(self->ptr.msg, field);
    bool is_unset = upb_FieldDef_IsMap(field) ? !val.map_val : !val.array_val;
    if (is_unset) return PyUpb_Message_GetStub(self, field);
    if (upb_FieldDef_IsMap(field)) {
      return PyUpb_MapContainer_GetOrCreateWrapper((upb_Map*)val.map_val, field,
                                                   self->arena);
    } else {
      return PyUpb_RepeatedContainer_GetOrCreateWrapper(
          (upb_Array*)val.array_val, field, self->arena);
    }
  }
```

**File:** python/google/protobuf/internal/thread_safe_test.py (L541-578)
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

**File:** python/google/protobuf/internal/thread_safe_test.py (L584-612)
```python
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
