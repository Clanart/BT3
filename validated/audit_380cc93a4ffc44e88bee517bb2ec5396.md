### Title
Data race in `upb_Message_GetOrPromoteExtension` corrupts shared unknown-field/extension storage on concurrent access to a parsed message - (File: `upb/message/promote.c`)

### Summary
The Linux kernel CVE-2024-53124 stems from a socket's forward-allocation counter (`sk->sk_forward_alloc`) being mutated by two threads concurrently via `sk_forward_alloc_add()` without the expected `sk_lock` protection, corrupting shared accounting state. The protobuf analog is `upb_Message_GetOrPromoteExtension()` in `upb/message/promote.c`, which performs an unsynchronized read-modify-write sequence (scan unknown fields → parse → delete unknown entries → insert a new extension entry) directly on the internal `upb_Message` unknown/extension storage. This function is reachable from ordinary, documented "read" accessors (`GetExtension`/`HasExtension`) on any parsed message, and the core upb C API provides **no locking** around it — locking was only bolted on as a stop-gap in the `hpb` C++ wrapper.

### Finding Description
`upb_Message_GetOrPromoteExtension` walks the message's unknown-field/extension linked list (`upb_TaggedAuxPtr` chain, `upb/message/internal/message.h`), looking for unknown bytes matching an extension's field number, parses them into a new sub-message, and then mutates the message twice: it deletes the matching unknown entries via `upb_Message_DeleteUnknown2` and inserts the parsed result via `_upb_Message_GetOrCreateExtension`: [1](#0-0) [2](#0-1) 

None of this is protected by any lock or atomic operation in the core upb runtime — the function only asserts the message is not "frozen" (`UPB_ASSERT(!upb_Message_IsFrozen(msg))`), which is a debug-only check, not a race-preventing mechanism.

Protobuf's own engineers have identified and only partially mitigated exactly this class of bug. In `hpb/internal/message_lock.h`, a temporary, opt-in locking shim was introduced specifically to guard `GetOrPromoteExtension`, `Serialize`, `DeepCopy`, and `DeepClone`: [3](#0-2) 

and the corresponding wrapper in `message_lock.cc` explicitly `const_cast`s away constness to call the mutating promotion routine while holding a pluggable lock: [4](#0-3) 

This confirms the invariant that failed: **a parsed `upb_Message*` handed to multiple threads for "read-only" extension access is not actually safe to read concurrently**, because the get-path silently mutates shared internal state (unknown-field list and extension list) whenever an extension has not yet been promoted from raw unknown bytes. The `hpb` fix is described in its own comment as "Temporary... Will be replaced by a core runtime solution in the future," meaning the underlying core upb C API (used directly by Python-upb, Ruby, PHP-upb, Lua, and any C/C++ code calling `upb_Message_GetOrPromoteExtension`/generated `_get_ext` accessors without going through `hpb`) still has **no such protection**.

This directly parallels the kernel bug's failed invariant: a data structure that call sites assume is either read-only or serialized by an external lock is in fact mutated on a hot, commonly-taken path, and two threads racing on the same shared object corrupt the object's internal bookkeeping structures (unknown-field linked list / tagged-pointer array) rather than a scalar counter.

### Impact Explanation
Where the kernel bug corrupts an accounting counter (leading to a WARN and eventually leaked/negative forward-alloc accounting — an integrity issue, not memory corruption), the protobuf analog is more severe: the racing operations mutate a linked list of tagged pointers and free/reallocate unknown-field storage (`upb_Message_DeleteUnknown2` frees/moves unknown entries in the arena-backed list) concurrently. Two threads racing through `upb_Message_GetOrPromoteExtension` on the same `upb_Message*` can:
- Both observe "not yet promoted," both parse and both attempt to delete the same unknown entries and insert an extension entry, corrupting the tagged-pointer chain (lost updates, double insertion, or iteration over a partially-mutated list).
- This is a genuine memory-safety hazard (heap/list corruption), not just an accounting nit, given the underlying structure is a set of raw pointers with manual tagging (`upb_TaggedAuxPtr`) rather than an atomic/lock-protected structure.

This is realistically triggerable by an ordinary client: an application that parses one message once (e.g., caches a decoded request/response) and then serves it to multiple worker threads calling `GetExtension()` on it — a usage pattern that is not flagged as unsafe by upb's public API/documentation for extension access, unlike the well-documented "messages are not thread-safe for mutation" rule for direct field setters.

### Likelihood Explanation
The trigger conditions are narrow but realistic: the extension must not yet be "canonical" in the message (i.e., it still exists only as unknown bytes or a non-canonical extension after parsing — a normal state for a message decoded without the extension's mini-table linked, or one accessed via a different `upb_MiniTableExtension` than at parse time), and two threads must call an extension accessor on the same message pointer at approximately the same time. This is plausible for schema/registry setups using unlinked extensions (a documented and supported upb feature), and Google's own hpb team clearly considered this reachable/important enough to add a locking shim — confirming feasibility, not just theoretical risk. However, the core (non-hpb) surface is not covered by any test demonstrating the race is fixed there; I could not verify from the available files whether Python/Ruby/PHP-upb bindings route all extension access through a lock equivalent to `hpb`'s.

### Recommendation
- Push the `hpb::internal::MessageLock` protection (or an equivalent lock/atomic promotion protocol) down into the core `upb_Message_GetOrPromoteExtension` and `upb_MiniTable_PromoteUnknownTo*` APIs in `upb/message/promote.c`, rather than leaving it as an opt-in shim only used by `hpb`.
- Alternatively, make extension/unknown-field promotion idempotent and safe under concurrent execution using atomic CAS on the `upb_TaggedAuxPtr` slot (mirroring the lock-free lazy-pointer pattern already used elsewhere in the codebase, e.g. `python/free_threading/lazy_ptr.h`), so that racing promotions converge on a single winner without corrupting the list.
- Document explicitly (in `upb_Message_GetOrPromoteExtension`'s header comment in `upb/message/promote.h`) that concurrent extension access on unpromoted extensions is unsafe until this is fixed, since the current comment only warns about non-message extension types, not about concurrency.

### Proof of Concept
A full multi-threaded reproduction was not run (no terminal access in this environment); the following is a minimal, code-grounded scenario derived directly from existing project tests demonstrating the exact call path that is unsynchronized:

1. Build a message with an extension mini-table not linked at parse time, so the extension is parsed as unknown/non-canonical data — exactly as `upb/message/promote_test.cc`'s `PromoteFromMultiple`/`PromoteNonCanonicalExtension` tests set up: [5](#0-4) 
2. From two threads, concurrently call `upb_Message_GetOrPromoteExtension(msg, ext_table, options, arena, &value)` on the **same** `msg` pointer (no `hpb::internal::MessageLock` present, since this is a raw core-upb call as used in `promote_test.cc` and reachable from generated accessor code).
3. Both threads will race through the "check unknown fields → parse → delete unknown → create extension" sequence in `upb_Message_GetOrPromoteExtension` (`upb/message/promote.c:118-215`) without any lock, since the function only has `UPB_ASSERT(!upb_Message_IsFrozen(msg))` — a debug assertion, not a synchronization primitive.
4. Expected/documented-safe outcome per current usage patterns: identical result on both threads with no corruption. Actual risk: the tagged-pointer list is mutated by both threads simultaneously, exposing the exact class of bug the `hpb` team mitigated with `MessageLock` — confirming this is a known-real hazard class, not a hypothetical one, even though I could not execute the race to observe the corrupted state directly in this session.

### Citations

**File:** upb/message/promote.c (L118-128)
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
```

**File:** upb/message/promote.c (L193-215)
```c
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

**File:** hpb/internal/message_lock.h (L17-31)
```text
namespace hpb::internal {

// TODO: Temporary locking api for cross-language
// concurrency issue around extension api that uses lazy promotion
// from unknown data to upb_MiniTableExtension. Will be replaced by
// a core runtime solution in the future.
//
// Any api(s) using unknown or extension data (GetOrPromoteExtension,
// Serialize and others) call lock/unlock to provide a way for
// mixed language implementations to avoid race conditions)
using UpbExtensionUnlocker = void (*)(const void*);
using UpbExtensionLocker = UpbExtensionUnlocker (*)(const void*);

// TODO: Expose as function instead of global.
extern std::atomic<UpbExtensionLocker> upb_extension_locker_global;
```

**File:** hpb/internal/message_lock.cc (L66-75)
```text
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

**File:** upb/message/promote_test.cc (L98-131)
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
```
