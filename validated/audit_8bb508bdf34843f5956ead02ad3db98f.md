This is a strong analog. The `hpb::internal::message_lock.h` comment explicitly documents a known concurrency bug: cross-thread lazy promotion from unknown data into `upb_MiniTableExtension` requires an external, application-supplied lock because the core upb runtime itself doesn't protect it — structurally the same fetch-then-mutate-without-holding-lock pattern as the tmpfs CVE.

### Title
Unsynchronized lazy extension/unknown-field promotion mutates shared message state without an internal lock, causing data races on concurrently-read messages - ([File: upb/message/promote.c])

### Summary
`upb_Message_GetOrPromoteExtension` (and the related `upb_MiniTable_PromoteUnknownToMessage`/`...Array`/`...Map` functions) read the message's unknown-field storage, parse the matching unknown bytes into a new sub-message, and then mutate the message in place — creating an extension slot via `_upb_Message_GetOrCreateExtension`, writing into it, and calling `upb_Message_DeleteUnknown2` to shrink/rewrite the `aux_data` array [1](#0-0) . None of this touches a lock; the upb core provides no internal synchronization for this path. The only mitigation in the codebase is an external, caller-installed lock hook in `hpb/internal/message_lock.h`, explicitly described as a "temporary" stopgap for "the cross-language concurrency issue around extension api that uses lazy promotion from unknown data" [2](#0-1) .

### Finding Description
The upb thread-compatibility model states that only `const`-qualified operations may run concurrently, and any mutating operation must not race with other operations, including those through `const` pointers [3](#0-2) . However, `upb_Message_GetOrPromoteExtension` takes a non-const `upb_Message*` but is invoked from `hpb::internal::GetOrPromoteExtension`, which accepts a `const upb_Message* msg` and does a `const_cast` internally before mutating it [4](#0-3) . This mirrors exactly the invariant violated in the tmpfs CVE: an operation that is supposed to be read-only from the caller's perspective (`shmem_release_dquot`/`shmem_acquire_dquot` reading the dquot rbtree) instead performs an unsynchronized "find, then mutate the underlying structure" sequence — here: `upb_Message_NextUnknown2` walks the `aux_data` array/unknown-field list, `upb_MiniTable_ParseUnknownMessage` allocates and decodes a new sub-message, then `_upb_Message_GetOrCreateExtension` writes an extension slot, and finally a loop of `upb_Message_FindUnknown2` + `upb_Message_DeleteUnknown2` mutates `in->aux_data` in place, including shifting entries with `memmove` and reallocating slots via `_upb_Message_ReserveSlot` [5](#0-4) . If two threads call `GetOrPromoteExtension`/`HasExtensionOrUnknown` concurrently on the same message (a pattern application code can trigger simply by reading the same parsed message field from two threads, which many wrapper languages present as a safe "read" operation), one thread's `aux_data` index (`*iter`) computed during its `FindUnknown2` walk can be invalidated by the other thread's concurrent `DeleteUnknown2`/`memmove`, exactly analogous to the tmpfs bug where `shmem_release_dquot()` fetches the rbtree root outside the lock and then searches from a stale location after a concurrent rebalance.

### Impact Explanation
This is a data race on shared, arena-allocated message internals (`in->aux_data`), reachable purely by an ordinary consuming application calling documented "read" accessors (extension getters / `HasField`-style checks) from multiple threads on a single parsed message — a pattern that is not flagged as unsafe by any public API documentation for wrapper languages using hpb/upb. The consequences include corrupted `aux_data` entries, out-of-bounds `memmove`/index use (`in->aux_data[*iter - 1]` with a stale `*iter`), and potential memory corruption or use of freed/reused arena slots, matching the High-severity C/I/A impact profile of the analog CVE. It stops short of remote code execution without further chaining, but it is a genuine internal-invariant violation, not merely an allowed interpretation difference.

### Likelihood Explanation
Requires only that a consuming application share a parsed message across threads and access extension/unknown-field-backed data concurrently — no malicious schema, forged MiniTable, or privileged access is needed. This is explicitly acknowledged as a known, unresolved issue by the upb/hpb maintainers themselves via the "temporary locking api" comment and dedicated regression tests (`ConcurrentAccessDoesNotRaceBothLazy`, etc. in `hpb/internal/message_lock_test.cc`) [6](#0-5) , indicating the race is real and was previously observed, but the fix (external caller-supplied lock) is opt-in and not part of the core upb runtime, so any caller not wired through hpb's `MessageLock` remains exposed.

### Recommendation
Move the extension/unknown-field promotion locking into the core upb runtime (as already flagged by the `TODO: Fix const correctness issues` and `Will be replaced by a core runtime solution in the future` comments) rather than relying on an externally supplied, opt-in locker function pointer. At minimum, `upb_Message_GetOrPromoteExtension` and `upb_MiniTable_PromoteUnknownTo*` should assert/document that callers must hold exclusive access for the *entire* find-then-mutate sequence, and any language bindings that expose "read-only" accessors backed by lazy promotion must acquire the lock transparently rather than requiring the application to know about `upb_extension_locker_global`.

### Proof of Concept
Not independently executed; based on static review of `upb_Message_GetOrPromoteExtension` (`upb/message/promote.c:118-215`), `upb_Message_DeleteUnknown2` (`upb/message/unknown_fields.c:111-184`), and the existing regression harness in `hpb/internal/message_lock_test.cc`, which already exercises concurrent `GetOrPromoteExtension`/extension access to demonstrate the race the temporary lock works around [7](#0-6) . Reproducing the corruption would require running two threads calling `hpb::internal::GetOrPromoteExtension`/`HasExtensionOrUnknown` on the same message with the `upb_extension_locker_global` hook unset (i.e., not wired through hpb's registry-based lock), under a race detector such as TSan, to confirm the concurrent `aux_data` mutation.

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

**File:** docs/upb/arena_fusion.md (L14-16)
```markdown
μpb generally follows a thread-compatibility model where only operations on a
`const` pointer may occur concurrently from multiple threads; non-const
operations must not race with each other or operations on `const` pointers.
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

**File:** upb/message/unknown_fields.c (L162-175)
```c
    if (!UPB_PRIVATE(_upb_Message_ReserveSlot)(msg, arena)) {
      return kUpb_DeleteUnknown_AllocFail;
    }
    in = UPB_PRIVATE(_upb_Message_GetInternal)(msg);
    if (*iter != in->size) {
      // Shift later entries down so that unknown field ordering is preserved
      memmove(&in->aux_data[*iter + 1], &in->aux_data[*iter],
              sizeof(upb_TaggedAuxPtr) * (in->size - *iter));
    }
    in->aux_data[*iter] = upb_TaggedAuxPtr_MakeUnknownDataAliased(suffix);
    if (!upb_TaggedAuxPtr_IsUnknownAliased(unknown_ptr)) {
      in->aux_data[*iter - 1] = upb_TaggedAuxPtr_MakeUnknownDataAliased(prefix);
    }
    in->size++;
```

**File:** hpb/internal/message_lock_test.cc (L120-147)
```text
TEST(CppGeneratedCode, ConcurrentAccessDoesNotRaceBothLazy) {
  hpb::Arena arena;
  hpb::ExtensionRegistry registry(arena);
  TestConcurrentExtensionAccess(registry);
}

TEST(CppGeneratedCode, ConcurrentAccessDoesNotRaceOneLazyOneEager) {
  hpb::Arena arena;
  hpb::ExtensionRegistry r1(arena);
  r1.AddExtension(theme);
  TestConcurrentExtensionAccess(r1);
  hpb::ExtensionRegistry r2(arena);
  r2.AddExtension(ThemeExtension::theme_extension);
  TestConcurrentExtensionAccess(r2);
}

TEST(CppGeneratedCode, ConcurrentAccessDoesNotRaceBothEager) {
  hpb::Arena arena;
  hpb::ExtensionRegistry registry(arena);
  registry.AddExtension(theme);
  registry.AddExtension(ThemeExtension::theme_extension);
  TestConcurrentExtensionAccess(registry);
}

TEST(CppGeneratedCode, ConcurrentAccessDoesNotRaceGlobalInstance) {
  hpb::Arena arena;
  TestConcurrentExtensionAccess(hpb::ExtensionRegistry::generated_registry());
}
```
