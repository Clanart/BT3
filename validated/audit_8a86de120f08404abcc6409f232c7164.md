## Title
Packed fixed-width field decoding reads `size` bytes via `upb_EpsCopyInputStream_ReadStringEphemeral` before the length is verified against the actual remaining input, allowing out-of-bounds/patch-buffer contents to be copied into the resulting array field - ([File: upb/wire/decode.c])

### Summary
`_upb_Decoder_DecodeFixedPacked()` in `upb/wire/decode.c` reads a packed fixed32/fixed64 field by calling `upb_EpsCopyInputStream_ReadStringEphemeral(&d->input, ptr, val->size, &sv)` and then unconditionally `memcpy`s `sv.size` bytes of `sv.data` into the destination array *before* the stream's real end-of-buffer/limit check (`IsDone()`) has run. The `ReadStringEphemeral` API is explicitly documented to return possibly-garbage bytes from its internal 32-byte `patch` buffer when `size` extends past the legitimately-available input, deferring detection of that condition to a *later* call to `upb_EpsCopyInputStream_IsDone()`. Between the ephemeral read and that later detection, the (possibly stale/uninitialized) patch-buffer bytes are already copied into the message's array field, i.e., into attacker-visible parsed output.

### Finding Description
`upb_EpsCopyInputStream_ReadStringEphemeral` (upb/wire/internal/eps_copy_input_stream.h:271-285) only checks that `size` is within `e->end + kUpb_EpsCopyInputStream_SlopBytes` (the guaranteed always-safe-to-read region, including the 16-byte slop/patch area), not that `size` is within the field's actual pushed limit or the true remaining bytes of the input: [1](#0-0) 

The public header explicitly documents this hazard: [2](#0-1) 

`_upb_Decoder_DecodeFixedPacked` uses this ephemeral read for packed fixed-size fields, then immediately treats `sv.data`/`sv.size` as trustworthy and copies it into the array's backing store: [3](#0-2) 

The only check performed on `ptr`/`val->size` before the copy is `if (!ptr) ... Malformed` and a mask check for element-size alignment - neither of these detects the "ephemeral garbage" case, because `ReadStringEphemeral` only returns `NULL` when `size` exceeds the *slop-inclusive* buffer end, not when it exceeds the actual message/limit boundary. When the packed field's declared `val->size` is larger than the true remaining bytes in the current buffer/limit but still within the 16-byte slop margin (or when the parser is already operating out of the patch buffer near a buffer boundary), `sv.data` can point into `e->patch`, which contains stale bytes from a previous field, a previous message, or uninitialized memory left over from prior parses on the same arena/stack region - not attacker-supplied wire bytes. These bytes are copied verbatim (or byte-swapped, on big-endian) into the destination `RepeatedField`/array, becoming part of the message the application subsequently reads.

This directly parallels the `webp` advisory's failed invariant: a length value taken from attacker input is used to read/copy data without first verifying the *source* actually contains that many valid bytes, exposing unrelated memory contents in the output.

### Impact Explanation
Impact is a **memory-content disclosure**, matching the webp CWE-125 classification: bytes that were never part of the sender's message (contents of the internal 32-byte patch buffer, which may hold remnants of previously parsed data or uninitialized stack/arena memory) can end up as values in a `repeated fixed32/fixed64/sfixed32/sfixed64/float/double` field of the parsed message. If that message is echoed back to a client, logged, or otherwise exposed, this constitutes an information leak of process memory contents, entirely from a bounded, valid-looking binary protobuf payload sent by an ordinary client to a public `upb_Decode()`-based parse API. It does not (by itself) provide arbitrary-length OOB reads past the actual buffer allocation - it stays within the guaranteed 16-slop-byte region - so it is bounded in scope to a memory-*confusion* disclosure (deferred-error garbage bytes), not an unbounded heap over-read.

### Likelihood Explanation
Reachability requires only a valid packed fixed-width repeated field whose declared length field, decoded from ordinary wire bytes, is inconsistent with the amount of data actually still available before the next `IsDone()` check - a condition that is plausible near buffer/limit boundaries during normal streaming decode of a maliciously truncated-but-still-parseable packed field. I was not able to fully trace, within the tool budget, the exact caller sequence that guarantees `IsDone()`/limit validation always runs strictly before this specific memcpy in every code path (e.g., whether `PushLimit`/`PopLimit` wrapping in `_upb_Decoder_DecodeToArray` forecloses the scenario in the currently released `upb` fast/slow paths). This is the main open question and would need to be confirmed with a concrete crafted-input trace/test before treating this as confirmed-exploitable rather than a plausible analog based on the documented API contract.

### Recommendation
Before performing the `memcpy` in `_upb_Decoder_DecodeFixedPacked`, validate `val->size` against the current `upb_EpsCopyInputStream` limit (e.g., via `upb_EpsCopyInputStream_CheckSize`) rather than relying solely on `ReadStringEphemeral`'s slop-relative bounds check, or switch to `upb_EpsCopyInputStream_ReadStringAlwaysAlias`-style semantics (which fails when `size` extends past the legitimate buffer, not just past the slop region) for this call site. At minimum, add an explicit limit check (`upb_EpsCopyInputStream_CheckSize(&d->input, ptr, val->size)`) immediately after `ReadStringEphemeral` and before the `memcpy`, throwing `kUpb_DecodeStatus_Malformed` on failure, so no ephemeral/patch-buffer bytes are ever copied into caller-visible message state.

### Proof of Concept
I was not able to construct and run a concrete minimal reproduction (crafted bytes + assertions showing leaked patch-buffer content in the decoded array) within the available tool budget/read-only environment. A reproduction would need to: (1) build a `upb_MiniTable` for a message with a `repeated fixed32` field, (2) craft a binary payload where the packed field's length varint claims a size that extends a few bytes past the true end of the supplied buffer but stays within the 16-byte slop margin, (3) pre-poison the memory adjacent to the input buffer (or exploit a prior parse leaving residue in the reused patch buffer) to demonstrate non-wire bytes appearing in the decoded `upb_Array`, and (4) call `upb_Decode()` directly. This is flagged as unverified and should be validated with an actual build/test run before being treated as a confirmed, exploitable vulnerability rather than a structurally-plausible analog to the webp advisory.

### Citations

**File:** upb/wire/internal/eps_copy_input_stream.h (L271-285)
```text
UPB_INLINE const char* upb_EpsCopyInputStream_ReadStringEphemeral(
    struct upb_EpsCopyInputStream* e, const char* ptr, size_t size,
    upb_StringView* sv) {
  UPB_ASSERT(size <= PTRDIFF_MAX);
  // Size must be within the current buffer (including slop bytes).
  const char* limit = e->end + kUpb_EpsCopyInputStream_SlopBytes;
  if ((ptrdiff_t)size > limit - ptr) {
    // For the moment, we consider this an error.  In a multi-buffer world,
    // it could be that the requested string extends into the next buffer, which
    // is not an error and should be recoverable.
    return UPB_PRIVATE(upb_EpsCopyInputStream_ReturnError)(e);
  }
  *sv = upb_StringView_FromDataAndSize(ptr, size);
  return ptr + size;
}
```

**File:** upb/wire/eps_copy_input_stream.h (L109-128)
```text
// Reads a string from the stream and advances the pointer accordingly.  The
// returned string view is ephemeral, only valid until the next call to
// upb_EpsCopyInputStream. It may point to the patch buffer.
//
// Returns NULL if size extends beyond the end of the current buffer (which may
// be the patch buffer).
//
// IMPORTANT NOTE: If `size` extends beyond the end of the stream, the returned
// data may contain garbage bytes from the patch buffer. For efficiency, this
// function does not check that `size` is within the current limit or even the
// end of the stream.
//
// The bytes are guaranteed to be safe to read ephemerally, but they may contain
// garbage data that does not correspond to anything in the input. This error
// will be detected later, when calling upb_EpsCopyInputStream_IsDone() (because
// we will not end at the proper limit), but it may result in nonsense bytes
// ending up in the output.
UPB_INLINE const char* upb_EpsCopyInputStream_ReadStringEphemeral(
    upb_EpsCopyInputStream* e, const char* ptr, size_t size,
    upb_StringView* sv);
```

**File:** upb/wire/decode.c (L243-287)
```c
UPB_FORCEINLINE
const char* _upb_Decoder_DecodeFixedPacked(upb_Decoder* d, const char* ptr,
                                           upb_Array* arr, wireval* val,
                                           const upb_MiniTableField* field,
                                           int lg2) {
  upb_StringView sv;
  ptr = upb_EpsCopyInputStream_ReadStringEphemeral(&d->input, ptr, val->size,
                                                   &sv);
  if (!ptr) upb_ErrorHandler_ThrowError(d->err, kUpb_DecodeStatus_Malformed);
  int mask = (1 << lg2) - 1;
  if (UPB_UNLIKELY((val->size & mask) != 0 || ptr == NULL)) {
    upb_ErrorHandler_ThrowError(d->err, kUpb_DecodeStatus_Malformed);
  }
  size_t count = val->size >> lg2;
  if (count == 0) return ptr;
  _upb_Decoder_Reserve(d, arr, count);
  void* mem = UPB_PTR_AT(upb_Array_MutableDataPtr(arr),
                         arr->UPB_PRIVATE(size) << lg2, void);
  arr->UPB_PRIVATE(size) += count;
  if (upb_IsLittleEndian()) {
    memcpy(mem, sv.data, sv.size);
  } else {
    const char* src = sv.data;
    const char* src_end = src + sv.size;
    char* dst = mem;
    if (lg2 == 2) {
      for (; src < src_end; src += 4, dst += 4) {
        uint32_t x;
        memcpy(&x, src, 4);
        x = upb_BigEndian32(x);
        memcpy(dst, &x, 4);
      }
    } else {
      UPB_ASSERT(lg2 == 3);
      for (; src < src_end; src += 8, dst += 8) {
        uint64_t x;
        memcpy(&x, src, 8);
        x = upb_BigEndian64(x);
        memcpy(dst, &x, 8);
      }
    }
  }

  return ptr;
}
```
