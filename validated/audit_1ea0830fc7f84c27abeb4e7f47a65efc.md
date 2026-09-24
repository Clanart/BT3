This is exactly the analog to the vmwgfx GEM race: `hpb::internal::MessageLock` (`hpb/internal/message_lock.h`/`.cc`) exists specifically because upb's `upb_Message_GetOrPromoteExtension` mutates a `upb_Message` (converting unknown wire bytes into a promoted extension in-place, deleting the underlying unknown-field storage) without any built-in synchronization, and this hpb-layer lock is a "TODO: Temporary locking api for cross-language concurrency issue" — an explicit acknowledgment that the underlying object mutation is unprotected outside of this narrow wrapper.

### Title
Missing built-in synchronization in `upb_Message_GetOrPromoteExtension` / lazy-unknown-to-extension promotion allows racing readers to observe corrupted message state - (File: `upb/message/promote.c`)

### Summary
`upb_Message_GetOrPromoteExtension` ( [1](#0-0) ) mutates a `upb_Message` object in place — it walks the unknown-field list, parses matching bytes into a newly allocated sub-message, installs it via `_upb_Message_GetOrCreateExtension`, and then deletes the original unknown-field entries ( [2](#0-1) ). This is the same class of flaw as CVE-2023-33951: an object (`upb_Message`, analogous to a GEM object) has an operation (extension "promotion", analogous to GEM buffer state changes) that mutates internal storage/linked lists, but the mutation is not itself guarded by any lock at the core-runtime layer.

### Finding Description
The `upb_Message` unknown-field list and extension list are ordinary mutable data structures with no atomic/lock protection in `upb/message/promote.c`. The comment in `hpb/internal/message_lock.h` states explicitly: *"Temporary locking api for cross-language concurrency issue around extension api that uses lazy promotion from unknown data to upb_MiniTableExtension... Any api(s) using unknown or extension data (GetOrPromoteExtension, Serialize and others) call lock/unlock to provide a way for mixed language implementations to avoid race conditions"* ( [3](#0-2) ). The lock is only wired up optionally through a global function pointer, `upb_extension_locker_global`, that must be installed by the embedding language runtime ( [4](#0-3) ; [5](#0-4) ). If no locker is installed — the default state — `unlocker_` is `nullptr` and `MessageLock` is a no-op ( [6](#0-5) ), so calling `upb_Message_GetOrPromoteExtension` directly (as many bindings and call sites do) or `Serialize`/`DeepCopy` concurrently with another thread doing the same on a shared message races on the unknown-list mutation and deletion.

This directly transfers the external invariant: vmwgfx's GEM objects needed to be locked during operations on the object to prevent racing readers from observing inconsistent/freed state; here, unknown-field/extension promotion likewise mutates and frees underlying storage (`upb_Message_DeleteUnknown2` reclaims the unknown bytes region after `ext->data.msg_val = extension_msg;`) while a concurrent reader iterating unknowns via `upb_Message_NextUnknown2`/serializing the message may observe a torn state.

The Java runtime shows the corresponding legitimate mitigation pattern that upb's core lacks by default: `InternalLazyField.ensureInitialized()` and `LazyFieldLite.ensureInitialized()` both take an explicit `synchronized (this)` block around the check-then-parse-then-publish sequence ( [7](#0-6) , [8](#0-7) ), and `toByteString()` is documented as "Returns a BytesString for this field in a thread-safe way" with its own synchronized block ( [9](#0-8) ). The Python runtime's own regression test, `testConcurrentLazyUnpackAndRead`, exists specifically to catch a race between a thread unpacking a lazy sub-message and a thread concurrently reading field presence/serializing the same shared message instance ( [10](#0-9) ) — confirming this exact bug class ("concurrent lazy unpack + read") is a real, previously-identified concern in the Protobuf codebase, not a hypothetical.

### Impact Explanation
Where two threads concurrently call `upb_Message_GetOrPromoteExtension` (or one calls it while another calls `Serialize`/iterates unknown fields) on a shared, unlocked `upb_Message` — which is fully possible since the C locker is opt-in and the plain C API (`upb_Message_GetOrPromoteExtension` in `upb/message/promote.c`) provides no locking at all — the unknown-field linked list can be mutated/freed by one thread while read by another. This can yield disclosure of stale/freed memory contents (reading unknown-field bytes after they were deleted/reused) or corrupted parses, matching CVE-2023-33951's C:H/I:N/A:N profile (information disclosure via racy access to an object under concurrent mutation) rather than availability/RCE impact.

### Likelihood Explanation
Medium. Exploitability requires an application built on `libupb` (or via `hpb`) to expose a shared `upb_Message` to concurrent access without installing `upb_extension_locker_global`, and to invoke extension-promoting/lazy-unknown APIs from more than one thread on the same message — a plausible but non-default configuration, and the hpb layer explicitly calls out this exact hazard as a known, currently-unresolved "TODO" needing "a core runtime solution in the future" ( [11](#0-10) ).

### Recommendation
Move the synchronization primitive into the core upb runtime (as the TODO already states is planned) rather than relying on an externally-installed, opt-in locker callback, so that `upb_Message_GetOrPromoteExtension`, `upb_Encode`/`Serialize`, `upb_Message_DeepCopy`/`DeepClone`, and unknown-field iteration/deletion are intrinsically safe for concurrent read/promote access on a shared message, mirroring the `synchronized` guarantees already provided by `LazyFieldLite`/`InternalLazyField` in the Java runtime.

### Proof of Concept
A minimal local reproduction (structurally, not executed here): allocate one `upb_Message` containing multiple unknown fields matching a given extension number (as constructed in `PromoteFromMultiple`, [12](#0-11) ), then spawn two threads that both call `upb_Message_GetOrPromoteExtension` on the same message/extension without installing `upb_extension_locker_global`. Because `upb_Message_GetOrPromoteExtension` reads/deletes unknown entries via `upb_Message_FindUnknown2`/`upb_Message_DeleteUnknown2` ( [13](#0-12) ) with no locking unless the hpb `MessageLock` wrapper is used, under a thread sanitizer this produces a data race on the message's unknown-field list — the C++ `hpb` test suite's `TestConcurrentExtensionAccess` (referenced by `ConcurrentAccessDoesNotRaceBothLazy` etc. in `hpb/internal/message_lock_test.cc`, lines 120-134) exists precisely to validate that the `MessageLock` wrapper (not the bare C API) prevents this race, implicitly confirming the bare API is unsafe without it.

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

**File:** upb/message/promote.c (L193-212)
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

**File:** hpb/internal/message_lock.h (L27-31)
```text
using UpbExtensionUnlocker = void (*)(const void*);
using UpbExtensionLocker = UpbExtensionUnlocker (*)(const void*);

// TODO: Expose as function instead of global.
extern std::atomic<UpbExtensionLocker> upb_extension_locker_global;
```

**File:** hpb/internal/message_lock.cc (L31-55)
```text
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
```

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L202-228)
```java
  private void ensureInitialized() throws InvalidProtocolBufferException {
    if (value != null) {
      return;
    }

    synchronized (this) {
      if (corrupted) {
        throw new InvalidProtocolBufferException("Repeat access to corrupted lazy field");
      }
      try {
        // `bytes` is guaranteed to be non-null since `value` was null.
        CodedInputStream input = bytes.newCodedInput();
        input.enableAliasing(/* enabled= */ true);
        // When lazyExtensionEnabled() returns true, it means all extensions including MessageSet's
        // will be fully parsed. When it returns false, it basically implies this can only be a
        // MessageSet extension, and we should fall back to the old behavior of silently returning
        // the default instance on corrupted extensions i.e. a full parse.
        value =
            extensionRegistry.lazyExtensionEnabled()
                ? defaultInstance.getParserForType().parsePartialFrom(input, extensionRegistry)
                : defaultInstance.getParserForType().parseFrom(input, extensionRegistry);
        input.checkLastTagWas(0);
      } catch (InvalidProtocolBufferException e) {
        corrupted = true;
        throw e;
      }
    }
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L406-427)
```java
  /** Returns a BytesString for this field in a thread-safe way. */
  public ByteString toByteString() {
    // We *must* return delayed bytes if it was set because the dependent messages may have
    // memoized serialized size based off of it.
    if (delayedBytes != null) {
      return delayedBytes;
    }
    if (memoizedBytes != null) {
      return memoizedBytes;
    }
    synchronized (this) {
      if (memoizedBytes != null) {
        return memoizedBytes;
      }
      if (value == null) {
        memoizedBytes = ByteString.EMPTY;
      } else {
        memoizedBytes = value.toByteString();
      }
      return memoizedBytes;
    }
  }
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L468-496)
```java
  /** Might lazily parse the bytes that were previously passed in. Is thread-safe. */
  protected void ensureInitialized(MessageLite defaultInstance) {
    if (value != null) {
      return;
    }
    synchronized (this) {
      if (value != null) {
        return;
      }
      try {
        if (delayedBytes != null) {
          // The extensionRegistry shouldn't be null here since we have delayedBytes.
          MessageLite parsedValue =
              defaultInstance.getParserForType().parseFrom(delayedBytes, extensionRegistry);
          this.value = parsedValue;
          this.memoizedBytes = delayedBytes;
        } else {
          this.value = defaultInstance;
          this.memoizedBytes = ByteString.EMPTY;
        }
      } catch (InvalidProtocolBufferException e) {
        // Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto
        // was invalid.
        this.corrupted = true;
        this.value = defaultInstance;
        this.memoizedBytes = ByteString.EMPTY;
      }
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
