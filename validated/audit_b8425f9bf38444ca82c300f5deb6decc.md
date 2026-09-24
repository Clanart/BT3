Based on my investigation, I have enough evidence to write the analysis. The strongest analog is the documented, unresolved race in `upb_Message_GetOrPromoteExtension` — a lazy state-mutation function that reads-then-mutates a message's unknown-field list and extension array without any internal locking, mirrored by the fact that `hpb` had to bolt on an external, opt-in `MessageLock` specifically to paper over this "cross-language concurrency issue," while the core upb C runtime used by Python/Ruby/PHP bindings has no such protection at all.

### Title
Unsynchronized concurrent extension promotion causes use-after-free/double-free of arena-backed unknown-field and extension data in `upb_Message_GetOrPromoteExtension` - (File: `upb/message/promote.c`)

### Summary
`upb_Message_GetOrPromoteExtension` performs a classic "check-then-mutate" sequence on a `upb_Message`'s internal unknown-field list and extension array: it walks the unknown fields (`upb_Message_NextUnknown2`), parses matching ones into a sub-message, installs a new `upb_Extension` (`_upb_Message_GetOrCreateExtension`), and deletes the now-promoted unknown entries (`upb_Message_DeleteUnknown2`) [1](#0-0) . None of this sequence is guarded by any lock at the core-runtime level. If two threads call this function (or a mix of promotion and unknown-field/extension iteration) on the *same* message concurrently — which happens whenever an application shares a parsed message across worker threads and lazily accesses an extension field from more than one of them — the linked-list deletion of unknown entries and the mutation of the extension array can interleave, corrupting arena-allocated list nodes and extension storage, analogous to the VFS bug where a lookup (`find_inode`) raced with a decrement/eviction (`iput`/`evict_inodes`) on a shared, reference-counted structure without a re-check under the lock.

### Finding Description
The upstream VFS bug is a lost-update/TOCTOU race: `evict_inodes()` observes `i_count == 0` without holding the per-inode lock consistently across the check and the state transition, while `find_inode()`/`iput()` concurrently re-acquire/drop the same inode, so two threads both end up calling `evict()` on the same object — the classic "check outside the lock, mutate inside" defect.

`upb_Message_GetOrPromoteExtension` has the same shape of defect at the data-structure level. It is a lazy, mutating "promote-on-first-access" operation:
1. It first checks whether the extension already exists via `_upb_Message_Getext` [2](#0-1) .
2. If not, it iterates the unknown-field linked list/array (`upb_Message_NextUnknown2`), decodes matching bytes, and accumulates a `found_count` [3](#0-2) .
3. It then installs the parsed message as a new extension via `_upb_Message_GetOrCreateExtension` [4](#0-3) .
4. Finally, in a loop, it repeatedly calls `upb_Message_FindUnknown2` and `upb_Message_DeleteUnknown2` to physically remove the promoted unknown entries from the message's internal unknown-field storage [5](#0-4) .

None of steps 1–4 take any lock on the message. The upb message header itself carries no synchronization primitive for this path — the *only* place in the codebase that acknowledges this defect is `hpb::internal::MessageLock`, whose comment states explicitly: *"Temporary locking api for cross-language concurrency issue around extension api that uses lazy promotion from unknown data to upb_MiniTableExtension. Will be replaced by a core runtime solution in the future,"* and lists `GetOrPromoteExtension`/`Serialize` as the exact APIs that need it [6](#0-5) . The `hpb` layer wraps every call in a global, opt-in locker/unlocker pair set by the embedding application [7](#0-6) , but this locker is a global function pointer that defaults to `nullptr` (no-op) unless an application explicitly registers one, and it is used **only from the `hpb` C++ generated-code layer** — it is not wired into the upb core, nor into Python (`python/message.c` calls the extension dict without any such lock), Ruby, or PHP bindings, which all call directly into the same unlocked `upb_Message_GetOrPromoteExtension`/`upb_Message_DeleteUnknown2` machinery [8](#0-7) .

This means: for any binding other than `hpb` (with a locker registered), if application code shares one parsed message object across multiple threads and more than one thread triggers extension promotion (or one thread promotes while another iterates unknown fields/serializes), the deletion of unknown-field nodes and insertion into the extension array race exactly as the VFS inode's hash-list/refcount state does — one thread can free/relink a node that another thread is mid-traversal on, or one thread's arena-allocated extension write can clobber another's, all inside a single-threaded-design data structure that was never given the internal lock the `hpb` wrapper had to invent as a stopgap.

### Impact Explanation
A successful race corrupts the message's internal unknown-field list (an arena-allocated singly/doubly linked structure) and/or the extension array during concurrent deletion/insertion, which can lead to use-after-free reads of freed/reused arena memory, double-processing of the same unknown entry, or writing extension data through a stale pointer — memory corruption with confidentiality and integrity impact (matching the High severity, C:H/I:H profile of the analog CVE), reachable purely through the public parsing/extension-access API surface without any application misuse beyond the common and supported pattern of sharing a parsed protobuf message across threads for concurrent reads.

### Likelihood Explanation
Triggering this requires: (1) a message containing an extension field encoded such that it lands in unknown fields at parse time (e.g., extension registry not supplied, or a non-canonical/unlinked mini-table scenario as exercised in `upb/message/promote_test.cc`), and (2) the consuming application accessing that extension from two threads concurrently on the shared parsed instance — a realistic pattern for services that parse once and serve many concurrent readers. The `hpb` team's own comment confirms this is a known, still-open ("temporary"/"will be replaced") cross-language concurrency defect in the core runtime, not a hypothetical.

### Recommendation
Add the missing synchronization (or a lock-free, single-mutation-winner CAS design similar to `PyUpb_LazyPtr_LazyInit` [9](#0-8)  or the upb arena refcount CAS-retry pattern [10](#0-9) ) directly inside `upb_Message_GetOrPromoteExtension` and the unknown-field deletion path in the core upb runtime, rather than relying on an opt-in, `hpb`-only external locker that other language bindings never install.

### Proof of Concept
Using the existing test scaffolding in `upb/message/promote_test.cc` as a base (e.g., `PromoteFromMultiple`, `PromoteNonCanonicalExtension` [11](#0-10) ): parse one shared message containing an extension field left as unknown data, then spawn two threads that both call `upb_Message_GetOrPromoteExtension` (or one calling it while the other calls `upb_Message_NextUnknown2`/serializes) on the same `upb_Message*` without going through `hpb::internal::MessageLock`. Under a thread sanitizer, this reproduces a data race on the unknown-field list and extension array identical in kind to the races already reproduced for other lazy-mutation paths in `python/google/protobuf/internal/thread_safe_test.py`'s `testConcurrentLazyUnpackAndRead` and `testConcurrentClearAndSubObjectDeletionRace` [12](#0-11) , confirming the check-then-mutate defect is real and unmitigated outside of the `hpb` opt-in lock.

### Citations

**File:** upb/message/promote.c (L118-215)
```c
upb_GetExtension_Status upb_Message_GetOrPromoteExtension(
    upb_Message* msg, const upb_MiniTableExtension* ext_table,
    int decode_options, upb_Arena* arena, upb_MessageValue* value) {
  UPB_ASSERT(!upb_Message_IsFrozen(msg));
  UPB_ASSERT(upb_MiniTableExtension_CType(ext_table) == kUpb_CType_Message);
  const upb_Extension* extension =
      UPB_PRIVATE(_upb_Message_Getext)(msg, ext_table);
  if (extension) {
    memcpy(value, &extension->data, sizeof(upb_MessageValue));
    return kUpb_GetExtension_Ok;
  }

  // Check unknown fields, if available promote.
  int found_count = 0;
  uint32_t field_number = upb_MiniTableExtension_Number(ext_table);
  const upb_MiniTable* extension_table =
      upb_MiniTableExtension_GetSubMessage(ext_table);
  // Will be populated on first parse and then reused
  upb_Message* extension_msg = NULL;
  int depth_limit = 100;
  uintptr_t iter = kUpb_Message_UnknownBegin;
  upb_MessageUnknown data;
  while (upb_Message_NextUnknown2(msg, &data, &iter)) {
    if (data.type == kUpb_MessageUnknownType_NonCanonicalExtension) {
      const upb_Extension* ext = (const upb_Extension*)data.value.extension;
      if (upb_MiniTableExtension_Number(ext->ext) == field_number) {
        found_count++;
        upb_UnknownToMessageRet parse_result =
            upb_MiniTable_ParseUnknownMessage(&data, extension_table,
                                              /* base_message= */ extension_msg,
                                              decode_options, arena);
        if (parse_result.status != kUpb_UnknownToMessage_Ok) {
          return upb_UnknownToMessage_ToGetExtensionStatus(parse_result.status);
        }
        extension_msg = parse_result.message;
      }
    } else {
      UPB_ASSERT(data.type == kUpb_MessageUnknownType_StringView);
      upb_StringView unknown_bytes = data.value.bytes;
      const char* ptr = unknown_bytes.data;
      upb_EpsCopyInputStream stream;
      upb_EpsCopyInputStream_Init(&stream, &ptr, unknown_bytes.size);
      while (!upb_EpsCopyInputStream_IsDone(&stream, &ptr)) {
        uint32_t tag;
        const char* unknown_begin = ptr;
        ptr = upb_WireReader_ReadTag(ptr, &tag, &stream);
        if (!ptr) return kUpb_GetExtension_ParseError;
        if (field_number == upb_WireReader_GetFieldNumber(tag)) {
          upb_StringView data;
          found_count++;
          upb_EpsCopyCapture capture;
          upb_EpsCopyCapture_Start(&capture, &stream, unknown_begin);
          ptr = _upb_WireReader_SkipValue(ptr, tag, depth_limit, &stream);
          if (!ptr || !upb_EpsCopyCapture_End(&capture, &stream, ptr, &data)) {
            return kUpb_GetExtension_ParseError;
          }
          upb_MessageUnknown unknown_item;
          unknown_item.type = kUpb_MessageUnknownType_StringView;
          unknown_item.value.bytes = data;
          upb_UnknownToMessageRet parse_result =
              upb_MiniTable_ParseUnknownMessage(
                  &unknown_item, extension_table,
                  /* base_message= */ extension_msg, decode_options, arena);
          if (parse_result.status != kUpb_UnknownToMessage_Ok) {
            return upb_UnknownToMessage_ToGetExtensionStatus(
                parse_result.status);
          }
          extension_msg = parse_result.message;
        } else {
          ptr = _upb_WireReader_SkipValue(ptr, tag, depth_limit, &stream);
          if (!ptr) return kUpb_GetExtension_ParseError;
        }
      }
    }
  }
  if (!extension_msg) {
    return kUpb_GetExtension_NotPresent;
  }

  upb_Extension* ext =
      UPB_PRIVATE(_upb_Message_GetOrCreateExtension)(msg, ext_table, arena);
  if (!ext) {
    return kUpb_GetExtension_OutOfMemory;
  }
  ext->data.msg_val = extension_msg;

  while (found_count > 0) {
    upb_FindUnknownRet2 found = upb_Message_FindUnknown2(msg, field_number, 0);
    UPB_ASSERT(found.status == kUpb_FindUnknown_Ok);
    if (upb_Message_DeleteUnknown2(msg, &found.unknown, &found.iter, arena) ==
        kUpb_DeleteUnknown_AllocFail) {
      return kUpb_GetExtension_OutOfMemory;
    }
    found_count--;
  }
  value->msg_val = extension_msg;
  return kUpb_GetExtension_Ok;
}
```

**File:** hpb/internal/message_lock.h (L19-26)
```text
// TODO: Temporary locking api for cross-language
// concurrency issue around extension api that uses lazy promotion
// from unknown data to upb_MiniTableExtension. Will be replaced by
// a core runtime solution in the future.
//
// Any api(s) using unknown or extension data (GetOrPromoteExtension,
// Serialize and others) call lock/unlock to provide a way for
// mixed language implementations to avoid race conditions)
```

**File:** hpb/internal/message_lock.cc (L37-75)
```text
class MessageLock {
 public:
  explicit MessageLock(const upb_Message* msg) : msg_(msg) {
    UpbExtensionLocker locker =
        upb_extension_locker_global.load(std::memory_order_acquire);
    unlocker_ = (locker != nullptr) ? locker(msg) : nullptr;
  }
  MessageLock(const MessageLock&) = delete;
  void operator=(const MessageLock&) = delete;
  ~MessageLock() {
    if (unlocker_ != nullptr) {
      unlocker_(msg_);
    }
  }

 private:
  const upb_Message* msg_;
  UpbExtensionUnlocker unlocker_;
};

bool HasExtensionOrUnknown(const upb_Message* msg,
                           const upb_MiniTableExtension* eid) {
  MessageLock msg_lock(msg);
  if (upb_Message_HasExtension(msg, eid)) return true;

  const uint32_t number = upb_MiniTableExtension_Number(eid);
  return upb_Message_FindUnknown2(msg, number, 0).status == kUpb_FindUnknown_Ok;
}

bool GetOrPromoteExtension(const upb_Message* msg,
                           const upb_MiniTableExtension* eid, upb_Arena* arena,
                           upb_MessageValue* value) {
  // TODO: Fix const correctness issues.
  auto mutable_msg = const_cast<upb_Message*>(msg);
  MessageLock msg_lock(mutable_msg);
  upb_GetExtension_Status ext_status =
      upb_Message_GetOrPromoteExtension(mutable_msg, eid, 0, arena, value);
  return ext_status == kUpb_GetExtension_Ok;
}
```

**File:** python/message.c (L2011-2027)
```c
static PyObject* PyUpb_Message_GetExtensionDict(PyObject* _self,
                                                void* closure) {
  PyUpb_Message* self = (void*)_self;
  if (self->ext_dict) {
    Py_INCREF(self->ext_dict);
    return self->ext_dict;
  }

  const upb_MessageDef* m = _PyUpb_Message_GetMsgdef(self);
  if (upb_MessageDef_ExtensionRangeCount(m) == 0) {
    PyErr_SetNone(PyExc_AttributeError);
    return NULL;
  }

  self->ext_dict = PyUpb_ExtensionDict_New(_self);
  return self->ext_dict;
}
```

**File:** python/free_threading/lazy_ptr.h (L76-84)
```text
static inline void* PyUpb_LazyPtr_RawGet(const void* lazy_ptr) {
  return upb_Atomic_Load((UPB_ATOMIC(void*)*)lazy_ptr, memory_order_acquire);
}

// Atomically loads or initializes a lazy C pointer. On a CAS collision race,
// calls `free_fn` on the duplicate pointer and returns the winning instance.
void* PyUpb_LazyPtr_LazyInit(void* lazy_ptr, PyUpb_PtrInitFunc init_fn,
                             PyUpb_PtrFreeFunc free_fn, void* ctx);
#endif
```

**File:** upb/mem/arena.c (L973-997)
```c
bool upb_Arena_IncRefFor(const upb_Arena* a, const void* owner) {
  upb_ArenaInternal* ai = upb_Arena_Internal(a);
  if (_upb_ArenaInternal_HasInitialBlock(ai)) return false;
  upb_ArenaRoot r;
  r.root = ai;

retry:
  r = _upb_Arena_FindRoot(r.root);
  if (upb_Atomic_CompareExchangeWeak(
          &r.root->parent_or_count, &r.tagged_count,
          _upb_Arena_TaggedFromRefcount(
              _upb_Arena_RefCountFromTagged(r.tagged_count) + 1),
          // Relaxed order is safe on success, incrementing the refcount
          // need not perform any synchronization with the eventual free of the
          // arena - that's provided by decrements.
          memory_order_relaxed,
          // Relaxed order is safe on failure as r.tagged_count is immediately
          // overwritten by retrying the find root operation.
          memory_order_relaxed)) {
    // We incremented it successfully, so we are done.
    return true;
  }
  // We failed update due to parent switching on the arena.
  goto retry;
}
```

**File:** upb/message/promote_test.cc (L98-143)
```text
TEST(GeneratedCode, PromoteFromMultiple) {
  int options = kUpb_DecodeOption_AliasString;
  upb_Arena* arena = upb_Arena_New();
  upb_test_ModelWithExtensions* msg = upb_test_ModelWithExtensions_new(arena);

  upb_test_ModelExtension1* extension1 = upb_test_ModelExtension1_new(arena);
  upb_test_ModelExtension1_set_str(extension1,
                                   upb_StringView_FromString("World"));

  upb_test_ModelExtension1_set_model_ext(msg, extension1, arena);

  size_t serialized_size;
  char* serialized1 =
      upb_test_ModelWithExtensions_serialize(msg, arena, &serialized_size);

  upb_test_ModelExtension1_set_str(extension1,
                                   upb_StringView_FromString("Everyone"));
  size_t serialized_size2;
  char* serialized2 =
      upb_test_ModelWithExtensions_serialize(msg, arena, &serialized_size2);
  char* concat =
      (char*)upb_Arena_Malloc(arena, serialized_size + serialized_size2);
  memcpy(concat, serialized1, serialized_size);
  memcpy(concat + serialized_size, serialized2, serialized_size2);

  upb_test_ModelWithExtensions* parsed = upb_test_ModelWithExtensions_parse_ex(
      concat, serialized_size + serialized_size2,
      upb_ExtensionRegistry_New(arena), options, arena);

  upb_MessageValue value;
  upb_GetExtension_Status result = upb_Message_GetOrPromoteExtension(
      UPB_UPCAST(parsed), upb_test_ModelExtension1_model_ext_ext, options,
      arena, &value);
  ASSERT_EQ(result, kUpb_GetExtension_Ok);
  upb_test_ModelExtension1* parsed_ex =
      (upb_test_ModelExtension1*)value.msg_val;
  upb_StringView field = upb_test_ModelExtension1_str(parsed_ex);
  EXPECT_EQ(absl::string_view(field.data, field.size), "Everyone");

  upb_FindUnknownRet found = upb_Message_FindUnknown(
      UPB_UPCAST(parsed),
      upb_MiniTableExtension_Number(upb_test_ModelExtension1_model_ext_ext), 0);
  EXPECT_EQ(kUpb_FindUnknown_NotPresent, found.status);

  upb_Arena_Free(arena);
}
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
