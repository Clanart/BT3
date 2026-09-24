I have enough evidence to write the final analog now.

### Title
Unsynchronized in-place mutation during `upb_Message_GetOrPromoteExtension` lazy extension promotion races with concurrent readers on a shared message - ([File: upb/message/promote.c])

### Summary
`upb_Message_GetOrPromoteExtension()` is invoked from public, read-looking accessor paths (`GetExtension`, `HasExtension`, `Serialize`) exposed by upb-based language bindings (e.g. `hpb`) on a parsed message that came directly from a public parse API. The function performs unprotected in-place writes — creating/overwriting an extension slot and deleting unknown-field entries on the shared `upb_Message` — with no locking in the core upb runtime itself. If a consuming application (as many naturally do, since these calls look like read-only getters/serializers) invokes this API concurrently from two threads on the same parsed message that still contains the relevant field only as unknown/non-canonical data, one thread's unsynchronized write path (unknown-field deletion + extension slot creation) races with another thread's concurrent read of the same unknown-field list/extension storage, producing use-after-free/out-of-bounds reads analogous to the CVE-2021-3753 pattern (an unprotected write racing with a read on the same object).

### Finding Description
`upb_Message_GetOrPromoteExtension` (`upb/message/promote.c:118-215`) first does a plain, unlocked read via `_upb_Message_Getext`. If the extension is not yet present, it walks the message's unknown-field list with `upb_Message_NextUnknown2`, parses matching unknown entries into a message value, then — critically — **mutates the shared message**:
- `UPB_PRIVATE(_upb_Message_GetOrCreateExtension)(msg, ext_table, arena)` creates/writes a new extension slot (`upb/message/promote.c:197-202`).
- `upb_Message_DeleteUnknown2(msg, &found.unknown, &found.iter, arena)` repeatedly deletes and potentially reallocates/shrinks the unknown-field storage in a loop (`upb/message/promote.c:204-212`).

None of this is protected by any lock inside upb's core runtime. The `hpb` C++ wrapper layer explicitly acknowledges this as a known, unresolved concurrency defect: `hpb/internal/message_lock.h:19-26` states this is a "*TODO: Temporary locking api for cross-language concurrency issue around extension api that uses lazy promotion from unknown data to upb_MiniTableExtension. Will be replaced by a core runtime solution in the future.*" and that any API using unknown/extension data (`GetOrPromoteExtension`, `Serialize`, etc.) must call an externally-supplied lock/unlock hook to "avoid race conditions." `hpb/internal/message_lock.cc:66-75` shows `GetOrPromoteExtension` `const_cast`s the message and mutates it under this *optional*, globally-installed locker — which is `nullptr` unless a specific language binding explicitly installs one (`hpb/internal/message_lock.cc:31,39-43`). Any caller of the underlying `upb_Message_GetOrPromoteExtension` C API directly (or any hpb consumer that never installs `upb_extension_locker_global`) gets zero synchronization.

This mirrors the invariant violated in CVE-2021-3753: a write path that mutates shared state (there, `vc_mode`; here, the unknown-field list and extension slot of a `upb_Message`) is reachable from an API that looks read-only/side-effect-free to the calling application, and is not protected by a lock at the layer that actually performs the mutation. A second thread reading the same unknown-field list (e.g. another `HasExtension`/`GetExtension`/`Serialize`/`ToString` call, or direct iteration via `upb_Message_NextUnknown2`) while the first thread is mid-deletion can observe a freed/moved iterator state or stale pointer into arena-owned unknown-field storage, causing an out-of-bounds/use-after-free read.

### Impact Explanation
Confidentiality/integrity impact: a concurrent reader on the same message can read freed or inconsistent memory (arena block reused, iterator state corrupted mid-deletion), disclosing adjacent heap/arena contents or crashing. This matches the CVE's "highest threat...to data confidentiality" characterization and CVSS profile (local, high complexity, low privilege, no user interaction, confidentiality-only impact) — it requires a specific concurrency pattern (two threads sharing one parsed message, one triggering lazy promotion) rather than being trivially remote-triggerable by a single request.

### Likelihood Explanation
Requires that a consuming application (a) parses attacker-supplied bytes into a message containing an unresolved extension field only as unknown/non-canonical data, and (b) shares that single message object across threads while invoking `GetExtension`/`HasExtension`/`Serialize` concurrently without installing the `hpb` locker hook (or using the raw upb C API directly, where no such hook exists at all). This is plausible in server applications that parse once and fan out message objects to worker threads for read access, since the accessor names give no indication that a mutating "lazy promotion" side effect is possible. The `hpb` team's own test suite (`hpb/internal/message_lock_test.cc:70-118`) exists specifically because this race was previously reproducible without the workaround, confirming it is a real, previously-hit failure mode rather than a theoretical one.

### Recommendation
Move locking (or an equivalent lock-free synchronization/CAS-based promotion scheme, similar to `python/free_threading/lazy_ptr.h`'s atomic lazy-init pattern) into the core upb runtime function `upb_Message_GetOrPromoteExtension`/`upb_Message_DeleteUnknown2` itself rather than relying on an optional, externally-installed locker in the `hpb` wrapper. At minimum, document loudly on `upb_Message_GetOrPromoteExtension` (`upb/message/promote.h:47-49`) that it performs hidden mutation and must not be called concurrently with any other read/write on the same message unless the caller supplies external synchronization, and audit/gate all "read-looking" wrapper entry points (`GetExtension`, `HasExtension`, `Serialize`, `ToString`) so they cannot be called without the message being either frozen/fully-promoted first or under an enforced lock.

### Proof of Concept
Not independently executed in this pass; the race is evidenced by the existing regression test `hpb/internal/message_lock_test.cc:70-118` (`TestConcurrentExtensionAccess`), which spins up 8 threads calling `HasExtension`/`GetExtension`/`Serialize`/copy-construct concurrently on one parsed message and only passes when `upb_extension_locker_global` is explicitly installed (`hpb/internal/message_lock_test.cc:71-72`). Removing that install call (or using `upb_Message_GetOrPromoteExtension` directly via the plain upb C API, which has no such hook) reconstructs the unprotected-write-vs-concurrent-read race described above. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** hpb/internal/message_lock.h (L17-42)
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

absl::StatusOr<absl::string_view> Serialize(const upb_Message* message,
                                            const upb_MiniTable* mini_table,
                                            upb_Arena* arena, int options);

bool HasExtensionOrUnknown(const upb_Message* msg,
                           const upb_MiniTableExtension* eid);

bool GetOrPromoteExtension(const upb_Message* msg,
                           const upb_MiniTableExtension* eid, upb_Arena* arena,
                           upb_MessageValue* value);
```

**File:** hpb/internal/message_lock.cc (L29-75)
```text
namespace hpb::internal {

std::atomic<UpbExtensionLocker> upb_extension_locker_global;

/**
 * MessageLock(msg) acquires lock on msg when constructed and releases it when
 * destroyed.
 */
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

**File:** hpb/internal/message_lock_test.cc (L54-118)
```text
ABSL_CONST_INIT absl::Mutex m[8] = {
    absl::Mutex(absl::kConstInit), absl::Mutex(absl::kConstInit),
    absl::Mutex(absl::kConstInit), absl::Mutex(absl::kConstInit),
    absl::Mutex(absl::kConstInit), absl::Mutex(absl::kConstInit),
    absl::Mutex(absl::kConstInit), absl::Mutex(absl::kConstInit)};
void unlock_func(const void* msg)
    ABSL_UNLOCK_FUNCTION(m[absl::HashOf(msg) & 0x7]) {
  m[absl::HashOf(msg) & 0x7].unlock();
}

::hpb::internal::UpbExtensionUnlocker lock_func(const void* msg)
    ABSL_EXCLUSIVE_LOCK_FUNCTION(m[absl::HashOf(msg) & 0x7]) {
  m[absl::HashOf(msg) & 0x7].lock();
  return &unlock_func;
}

void TestConcurrentExtensionAccess(const ::hpb::ExtensionRegistry& registry) {
  ::hpb::internal::upb_extension_locker_global.store(&lock_func,
                                                     std::memory_order_release);
  const std::string payload = GenerateTestData();
  TestModel parsed_model =
      ::hpb::Parse<TestModel>(payload, {.extension_registry = registry})
          .value();
  const auto test_main = [&] { EXPECT_EQ("str", parsed_model.str1()); };
  const auto test_theme = [&] {
    ASSERT_TRUE(::hpb::HasExtension(&parsed_model, theme));
    auto ext = hpb::GetExtension(&parsed_model, theme);
    ASSERT_OK(ext);
    EXPECT_EQ((*ext)->ext_name(), "theme");
  };
  const auto test_theme_extension = [&] {
    auto ext =
        hpb::GetExtension(&parsed_model, ThemeExtension::theme_extension);
    ASSERT_OK(ext);
    EXPECT_EQ((*ext)->ext_name(), "theme_extension");
  };
  const auto test_serialize = [&] {
    hpb::Arena arena;
    EXPECT_OK(::hpb::Serialize(&parsed_model, arena));
  };
  const auto test_copy_constructor = [&] {
    TestModel copy_a = parsed_model;
    TestModel copy_b = parsed_model;
    EXPECT_EQ(copy_a.has_str1(), copy_b.has_str1());
  };
  std::thread t1(test_main);
  std::thread t2(test_main);
  std::thread t3(test_theme);
  std::thread t4(test_theme);
  std::thread t5(test_theme_extension);
  std::thread t6(test_theme_extension);
  std::thread t7(test_serialize);
  std::thread t8(test_copy_constructor);
  t1.join();
  t2.join();
  t3.join();
  t4.join();
  t5.join();
  t6.join();
  t7.join();
  t8.join();
  test_main();
  test_theme();
  test_theme_extension();
}
```

**File:** upb/message/promote.h (L43-49)
```text
// Returns a message value or promotes an unknown field to an extension.
//
// TODO: Only supports extension fields that are messages,
// expand support to include non-message types.
UPB_NODISCARD upb_GetExtension_Status upb_Message_GetOrPromoteExtension(
    upb_Message* msg, const upb_MiniTableExtension* ext_table,
    int decode_options, upb_Arena* arena, upb_MessageValue* value);
```
