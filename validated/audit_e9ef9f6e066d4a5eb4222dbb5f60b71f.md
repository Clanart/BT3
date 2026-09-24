### Title
Use-after-free / double-free risk from stale Python wrapper ownership across oneof switches during `MergeFrom`/`ParseFromString` - ([File: python/google/protobuf/pyext/message.cc])

### Summary
The renepay report describes a `tal_free` double-free caused because a heap object (`routefail`) was linked to the wrong lifetime scope (`route`) instead of the object that actually owns it (`payment`), so a race/ordering issue let the underlying object be freed before the code that manually freed it again. The transferable invariant is: **an owning wrapper object must be re-linked to the correct backing object's lifetime whenever that backing object's identity changes underneath it (e.g., via a union/oneof switch), or the wrapper will operate on/free stale memory.**

The protobuf C++/Python binding has the exact same class of hazard in oneof handling: Python-level message wrapper objects (`CMessage`) are cached in `self->composite_fields` and hold a raw pointer to a C++ submessage that lives at a `oneof` slot. `MergeFrom`/`ParseFromString` can switch which field is active in the oneof, which causes the C++ union machinery to free/replace the underlying submessage. If the Python wrapper isn't released and detached from that oneof entry *before* the C++-level clear/switch happens, the wrapper is left pointing at freed memory, and any Python-level `Clear()`/deletion on that stale wrapper double-frees or use-after-frees the C++ object — precisely analogous to routefail being freed once by the `route` teardown and once manually.

### Finding Description
`MergeFromStringImpl` in [1](#0-0)  explicitly documents this hazard:
```
// We parse into a temporary message first to detect oneof switches before
// modifying the target message. This allows us to release the wrappers
// for switching oneof fields in the target message before they are deleted
// by C++ during the merge, preventing use-after-free bugs.
```
The mitigation calls `MaybeReleaseOneofBeforeMerge(self, *temp_message)` before invoking `message->MergeFrom(*temp_message)` at [2](#0-1) , and the plain `MergeFrom` public API does the same at [3](#0-2) . The purpose of `MaybeReleaseOneofBeforeMerge` is to detach any live Python `CMessage` wrapper that is cached for a oneof field in `self->composite_fields` *before* the C++ layer's `Clear`/`MergeFrom` frees or reassigns the underlying oneof member — this is the same fix pattern as renepay's commit, which re-links `routefail`'s ownership to the correct object (`payment`) instead of the transient one (`route`) so its lifetime tracking cannot go stale.

The core invariant that must hold: whenever the C++ oneof storage for a submessage is about to be cleared/replaced (which happens on every switch of the active oneof case, e.g. via `clear_$oneof_name$()` in generated code, shown by `OneofMessage::GenerateClearingCode` at [4](#0-3) , which unconditionally does `delete $field_$` off-arena or poisons/clears on-arena), any external wrapper (Python `CMessage`, or an analogous held raw pointer) referencing the old submessage instance must be invalidated/detached first. If `MaybeReleaseOneofBeforeMerge` failed to run, or ran on the wrong object (analogous to renepay linking to `route` instead of `payment`), a stale `CMessage.message` pointer would remain, and a subsequent `Clear()` on that Python wrapper (which explicitly deletes the underlying C++ message when the arena is null) would free memory already freed by the earlier oneof clear/switch — a double free directly analogous to the `routefail_end`/`tal_free` crash.

### Impact Explanation
If the ordering/detachment invariant is violated (e.g., a code path that mutates the oneof without going through `MaybeReleaseOneofBeforeMerge`, or a future refactor that reorders these calls), the result is memory corruption in the Python protobuf extension: double-free or use-after-free of a heap-allocated (non-arena) `Message` object, reachable purely by having a client parse two protobuf payloads into related message objects with switching oneof fields through the public `MergeFrom`/`MergeFromString`/`ParseFromString` API. This is process-level memory corruption (crash, and in adversarial conditions potentially exploitable heap corruption), which is High severity by class, matching the severity class of the renepay double-free (crash/DoS, at minimum).

### Likelihood Explanation
Today the code appears to correctly guard this path: both `MergeFrom` and `MergeFromStringImpl` call `MaybeReleaseOneofBeforeMerge` prior to the actual merge/parse into the destination message, exactly mirroring the "link ownership to the right object before mutating" fix pattern from the renepay commit. I was not able to fully inspect `MaybeReleaseOneofBeforeMerge`'s and `FixupMessageAfterMerge`'s implementations within the tool budget (only grep hits were retrieved, not the function bodies) to confirm that every call-site and every mutation path (including reflection-based oneof mutation from Python, e.g. `WhichOneof`/`ClearField`/`SetInParent` paths) is equally protected. This is a **known, already-mitigated hazard class** rather than a confirmed present-day bypass; the likelihood of an unguarded regression is what should be checked, not a currently demonstrated bypass.

### Recommendation
- Audit every Python-extension code path that can change which field is "active" in a `oneof` (not just `MergeFrom`/`ParseFromString`, but also `ClearField`, `SetInParent`, attribute assignment through `cmessage`, and `CopyFrom`) to confirm `MaybeReleaseOneofBeforeMerge`-equivalent detachment always runs before the underlying C++ oneof storage is cleared/replaced.
- Add a regression test that specifically creates a Python wrapper for a oneof submessage, performs a merge that switches the oneof case, and then exercises the previously-returned wrapper (`Clear()`/attribute access) to assert no crash/double free — mirroring `TestRegressionOverwrittenLazyOneofDoesNotLeak` in [5](#0-4) , which already covers a related lazy-oneof-leak regression but not the double-free/UAF case for Python wrappers specifically.
- Ensure any new/refactored oneof-mutation entry points funnel through the shared "release wrapper before free" helper rather than reimplementing partial detachment logic, so the fix can't silently regress the way the renepay bug did ("I don't see how this could have happened" — i.e., subtle reordering bugs).

### Proof of Concept
Not independently reproduced in this session — the current code already contains a fix (`MaybeReleaseOneofBeforeMerge`) for this exact hazard, so no crash was observed by inspection alone. A concrete PoC would require constructing a Python extension test: build message `A` with a live wrapper for `oneof_field_msg` (`a.oneof_field_msg`), then call `a.MergeFrom(b)` where `b` sets a different member of the same oneof, then invoke `.Clear()`/deletion on the previously obtained wrapper object and check under a memory sanitizer whether it double-frees or use-after-frees the original submessage; I could not execute this in the current session due to lack of runtime/build tooling access.

### Citations

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

**File:** python/google/protobuf/pyext/message.cc (L2074-2123)
```text
static PyObject* MergeFromStringImpl(CMessage* self, PyObject* arg,
                                     bool is_cleared) {
  Py_buffer data;
  if (PyObject_GetBuffer(arg, &data, PyBUF_SIMPLE) < 0) {
    return nullptr;
  }
  auto cleanup_data = absl::MakeCleanup([&data] { PyBuffer_Release(&data); });

  Message* message = AssureWritable(self);
  if (message == nullptr) {
    return nullptr;
  }

  // We parse into a temporary message first to detect oneof switches before
  // modifying the target message. This allows us to release the wrappers
  // for switching oneof fields in the target message before they are deleted
  // by C++ during the merge, preventing use-after-free bugs.
  // We use heap allocation (nullptr arena) for the temporary message so that
  // it is collected immediately after the merge, avoiding wasting arena memory.
  std::unique_ptr<Message> temp_message;
  Message* merge_dst;

  if (is_cleared) {
    // Check that it is really empty.
    ABSL_DCHECK_EQ(message->ByteSizeLong(), 0);
    merge_dst = message;
  } else {
    temp_message.reset(message->New(nullptr));
    merge_dst = temp_message.get();
  }

  PyMessageFactory* factory = GetFactoryForMessage(self);
  int depth = allow_oversize_protos
                  ? INT_MAX
                  : io::CodedInputStream::GetDefaultRecursionLimit();
  const char* ptr;
  internal::ParseContext ctx(
      depth, false, &ptr,
      absl::string_view(static_cast<const char*>(data.buf), data.len));

  ctx.data().pool = factory->pool->pool->get();
  ctx.data().factory = factory->message_factory;

  ptr = merge_dst->_InternalParse(ptr, &ctx);

  if (is_cleared) {
    // If we merged into the final destination, fix up now before we might have
    // an early exit.
    FixupMessageAfterMerge(self);
  }
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

**File:** src/google/protobuf/compiler/cpp/field_generators/message_field.cc (L631-653)
```text
void OneofMessage::GenerateClearingCode(io::Printer* p) const {
  p->Emit({{"poison_or_clear",
            [&] {
              if (HasDescriptorMethods(field_->file(), options_)) {
                p->Emit(R"cc(
                  $pbi$::MaybePoisonAfterClear($field_$);
                )cc");
              } else {
                p->Emit(R"cc(
                  if ($field_$ != nullptr) {
                    $field_$->Clear();
                  }
                )cc");
              }
            }}},
          R"cc(
            if (GetArena() == nullptr) {
              delete $field_$;
            } else if ($pbi$::DebugHardenClearOneofMessageOnArena()) {
              $poison_or_clear$;
            }
          )cc");
}
```

**File:** src/google/protobuf/message_unittest.inc (L2453-2478)
```text
TEST(MESSAGE_TEST_NAME, TestRegressionOverwrittenLazyOneofDoesNotLeak) {
  UNITTEST::TestAllTypes message;
  auto* lazy = message.mutable_oneof_lazy_nested_message();
  // We need to add enough payload to make the lazy field overflow the SSO of
  // Cord. However, NestedMessage does not have enough fields for that. Just add
  // some unknown payload to it. Use something that the validator will allow to
  // stay as lazy.
  lazy->GetReflection()->MutableUnknownFields(lazy)->AddFixed64(10, 10);
  lazy->GetReflection()->MutableUnknownFields(lazy)->AddFixed64(11, 10);
  // Validate that the size is large enough.
  ASSERT_GT(lazy->ByteSizeLong(), 15);

  // Append two instances of the oneof: first the lazy field, then any other to
  // cause a switch during parsing.
  std::string str;
  ASSERT_TRUE(message.AppendToString(&str));
  message.set_oneof_uint32(7);
  ASSERT_TRUE(message.AppendToString(&str));

  EXPECT_TRUE(UNITTEST::TestAllTypes().ParseFromString(str));
  Arena arena;
  // This call had a bug where the LazyField was not destroyed in any way
  // causing the Cord inside it to leak its contents.
  EXPECT_TRUE(
      Arena::Create<UNITTEST::TestAllTypes>(&arena)->ParseFromString(str));
}
```
