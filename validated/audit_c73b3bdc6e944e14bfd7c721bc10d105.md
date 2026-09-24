### Title
Packed fixed-array decoding in upb reads unvalidated length into the slop/patch buffer, copying unspecified memory into decoded array elements - (upb/wire/decode.c)

### Summary
`_upb_Decoder_DecodeFixedPacked()` in upb (the core wire parser used by C++ protobuf's upb backend and by Python/PHP/Ruby bindings) decodes a packed fixed32/fixed64 array by calling `upb_EpsCopyInputStream_ReadStringEphemeral()` with the attacker-supplied packed-field byte length, then unconditionally `memcpy`s that many bytes into freshly-reserved array storage. `ReadStringEphemeral` only bounds-checks the requested size against the *current buffer's slop/patch window* (`e->end + kUpb_EpsCopyInputStream_SlopBytes`), not against the field's actual remaining wire bytes. This mirrors the oidvector defect class: a fixed-element-size vector type is copied using a length value that is checked for "fits the allocation" but not for "is actually backed by real serialized data," letting a few bytes of unrelated/stale process memory be copied into the decoded value before the mismatch is later detected.

### Finding Description
`_upb_Decoder_DecodeFixedPacked` reads the array payload as an ephemeral string view and copies it directly into the array's backing store: [1](#0-0) 

The function it relies on is explicitly documented to return data that "may contain garbage bytes from the patch buffer" when `size` extends past the true end of input, because the bounds check only verifies the request fits within the buffer/slop window, not within the actual field limit: [2](#0-1) 

The corresponding implementation performs exactly that check — against `e->end + kUpb_EpsCopyInputStream_SlopBytes`, i.e., the low-level buffer/patch boundary, and not against `e->limit` (the field's own declared length coming from `ReadSize`): [3](#0-2) 

The 16-byte "slop" region and its 32-byte doubled `patch` buffer are explicitly allowed to hold "unspecified" values past true end-of-input, by design, to avoid extra bounds checks per field: [4](#0-3) 

Once `sv` is obtained (potentially covering slop/patch bytes beyond the true serialized data), the bytes are memcpy'd straight into the array's storage — becoming part of the decoded message value — before the outer decode loop's `IsDoneStatus`/limit check has a chance to flag the length mismatch as malformed: [5](#0-4) 

This is architecturally analogous to the C++ (non-upb) fast-table implementation, which is more conservative: `EpsCopyInputStream::ReadPackedFixed` only memcpys `BytesAvailable(ptr)`-bounded blocks per buffer chunk and explicitly checks `size != block_size` to detect truncation before returning success: [6](#0-5) 

The upb fast-decode fixed-array handler (`upb_DecodeFast_PackedFixed`) similarly validates `size % valbytes` but derives array capacity from the caller-supplied `size` and relies on the caller (`upb_EpsCopyInputStream_TryParseDelimitedFast`) for bounds — that path checks against `e->limit_ptr`, the correct field limit, unlike the plain `_upb_Decoder_DecodeFixedPacked` path shown above: [7](#0-6) [8](#0-7) 

### Impact Explanation
If exploitable, an attacker sending a crafted binary protobuf message with a packed fixed32/fixed64 field whose declared length exceeds the actual remaining wire bytes by a few bytes (but still within the 16/32-byte slop/patch window) can cause a few bytes of adjacent unspecified memory (patch buffer contents, potentially containing residue from previous parses) to be written into the decoded array before the overall parse is flagged malformed. This is consistent in class and severity with the PostgreSQL oidvector report (a few bytes of memory disclosure via improper length validation on a fixed-size vector type), not an out-of-bounds read/crash and not RCE.

### Likelihood Explanation
Reachability requires only a bounded, crafted binary protobuf payload through a public parse API (`upb_Decode`) — no special privileges. However, actual disclosure to the attacker is contingent on the caller/binding surfacing a partially-populated message despite the decode ultimately returning a Malformed status once `IsDoneStatus`/limit checks run; upb's documented contract is that callers must check the decode status before trusting the message, and most bindings discard the message/arena on failure. I could not fully trace, within the available context, whether any consuming-application path (e.g., Python/PHP/Ruby bindings via `upb/wire/decode_fast` vs. the plain `decode.c` path) returns or otherwise exposes the array contents to the caller before/without checking the final decode status. This is the main uncertainty limiting confidence in end-to-end exploitability, similar to the original report's own acknowledgment that "attacks that arrange for confidential information in disclosed bytes seem unlikely."

### Recommendation
In `_upb_Decoder_DecodeFixedPacked` (upb/wire/decode.c), bound the read against the field's actual declared/remaining length (the pushed limit), not merely against the buffer's slop/patch window, mirroring the approach already used by `upb_EpsCopyInputStream_TryParseDelimitedFast`'s `limit_ptr` check and the C++ `EpsCopyInputStream::ReadPackedFixed`'s `BytesAvailable`-bounded chunking with an explicit truncation check. Ensure the array-copy step cannot execute before the length-vs-available-data validation completes.

### Proof of Concept
A minimal local reproduction would require: constructing an `upb_MiniTable` with a single packed fixed32/fixed64 repeated field, then feeding `upb_Decode()` a buffer whose final bytes declare a packed length slightly larger than the number of bytes actually remaining (but within the 16-byte slop margin), placed at the very end of the input so the shortfall falls inside the patch/slop region, and inspecting (via a debug build with `PoisonMemoryRegion`/ASan or by pre-filling the patch buffer with a sentinel pattern in a modified test harness) whether the resulting `upb_Array` contains sentinel/uninitialized bytes for the last element before the overall decode status is checked. I was not able to actually run this experiment in this environment — this PoC sketch is a proposed reproduction outline, not a verified result. Confirming actual, attacker-observable disclosure (as opposed to a documented-but-contained internal invariant) requires exercising this on a live build with an ASAN/MSAN-instrumented decode and inspecting the returned message before status is checked.

### Citations

**File:** upb/wire/decode.c (L243-284)
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

**File:** upb/wire/internal/eps_copy_input_stream.h (L25-54)
```text
// The maximum number of bytes a single protobuf field can take up in the
// wire format.  We only want to do one bounds check per field, so the input
// stream guarantees that after upb_EpsCopyInputStream_IsDone() is called,
// the decoder can read this many bytes without performing another bounds
// check.  The stream will copy into a patch buffer as necessary to guarantee
// this invariant. Since tags can only be up to 5 bytes, and a max-length scalar
// field can be 10 bytes, only 15 is required; but sizing up to 16 permits more
// efficient fixed size copies.
#define kUpb_EpsCopyInputStream_SlopBytes 16

struct upb_EpsCopyCapture {
  const char* start;  // Pointer to the beginning of the captured region.
};

struct upb_EpsCopyInputStream {
  const char* end;        // Can read up to SlopBytes bytes beyond this.
  const char* limit_ptr;  // For bounds checks, = end + UPB_MIN(limit, 0)
  uintptr_t input_delta;  // Diff between the original input pointer and patch
  const char* buffer_start;  // Pointer to the original input buffer
  ptrdiff_t limit;           // Submessage limit relative to end
  upb_ErrorHandler* err;     // Error handler to use when things go wrong.
  bool error;                // To distinguish between EOF and error.
#ifndef NDEBUG
  int guaranteed_bytes;
#endif
  // Allocate double the size of what's required; this permits a fixed-size copy
  // from the input buffer, regardless of how many bytes actually remain in the
  // input buffer.
  char patch[kUpb_EpsCopyInputStream_SlopBytes * 2];
};
```

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

**File:** upb/wire/internal/eps_copy_input_stream.h (L322-342)
```text
UPB_FORCEINLINE bool upb_EpsCopyInputStream_TryParseDelimitedFast(
    struct upb_EpsCopyInputStream* e, const char** ptr, size_t size,
    upb_EpsCopyInputStream_ParseDelimitedFunc* func, void* ctx) {
  UPB_ASSERT(size <= PTRDIFF_MAX);
  if ((ptrdiff_t)size > e->limit_ptr - *ptr) {
    return false;
  }

  // Fast case: Sub-message is <128 bytes and fits in the current buffer.
  // This means we can preserve limit/limit_ptr verbatim.
  const char* saved_limit_ptr = e->limit_ptr;
  ptrdiff_t saved_limit = e->limit;
  e->limit_ptr = *ptr + size;
  e->limit = e->limit_ptr - e->end;
  UPB_ASSERT(e->limit_ptr == e->end + UPB_MIN(0, e->limit));
  *ptr = func(e, *ptr, size, ctx);
  e->limit_ptr = saved_limit_ptr;
  e->limit = saved_limit;
  UPB_ASSERT(e->limit_ptr == e->end + UPB_MIN(0, e->limit));
  return true;
}
```

**File:** src/google/protobuf/parse_context.h (L1520-1560)
```text
template <typename T>
const char* EpsCopyInputStream::ReadPackedFixed(const char* ptr, Arena* arena,
                                                int size,
                                                RepeatedField<T>* out) {
  ABSL_DCHECK_EQ(arena, out->GetArena());
  GOOGLE_PROTOBUF_PARSER_ASSERT(ptr);
  int nbytes = BytesAvailable(ptr);
  while (size > nbytes) {
    int num = nbytes / sizeof(T);
    int old_entries = out->size();
    out->ReserveWithArena(arena, old_entries + num);
    int block_size = num * sizeof(T);
    auto dst = out->AddNAlreadyReserved(num);
#ifdef ABSL_IS_LITTLE_ENDIAN
    std::memcpy(dst, ptr, block_size);
#else
    for (int i = 0; i < num; i++)
      dst[i] = UnalignedLoad<T>(ptr + i * sizeof(T));
#endif
    size -= block_size;
    if (limit_ <= kSlopBytes) return nullptr;
    ptr = Next();
    if (ptr == nullptr) return nullptr;
    ptr += kSlopBytes - (nbytes - block_size);
    nbytes = BytesAvailable(ptr);
  }
  int num = size / sizeof(T);
  int block_size = num * sizeof(T);
  if (num == 0) return size == block_size ? ptr : nullptr;
  int old_entries = out->size();
  out->ReserveWithArena(arena, old_entries + num);
  auto dst = out->AddNAlreadyReserved(num);
#ifdef ABSL_IS_LITTLE_ENDIAN
  ABSL_CHECK(dst != nullptr) << out << "," << num;
  std::memcpy(dst, ptr, block_size);
#else
  for (int i = 0; i < num; i++) dst[i] = UnalignedLoad<T>(ptr + i * sizeof(T));
#endif
  ptr += block_size;
  if (size != block_size) return nullptr;
  return ptr;
```

**File:** upb/wire/decode_fast/field_fixed.c (L46-74)
```c
static const char* upb_DecodeFast_PackedFixed(upb_EpsCopyInputStream* st,
                                              const char* ptr, int size,
                                              void* ctx) {
  upb_DecodeFast_PackedFixedContext* c =
      (upb_DecodeFast_PackedFixedContext*)ctx;

  int valbytes = upb_DecodeFast_ValueBytes(c->type);

  if (size == 0) return ptr;  // 0-element packed fields are valid.

  if (size % valbytes != 0) {
    UPB_DECODEFAST_ERROR(c->d, kUpb_DecodeStatus_Malformed, c->ret);
    return NULL;
  }

  upb_DecodeFastArray arr;

  if (!upb_DecodeFast_GetArrayForAppend(c->d, ptr, c->msg, *c->data, c->hasbits,
                                        &arr, c->type, size / valbytes,
                                        c->ret)) {
    return NULL;
  }

  upb_DecodeFast_InlineMemcpy(arr.dst, ptr, size);
  arr.dst = UPB_PTR_AT(arr.dst, size, char);
  upb_DecodeFastField_SetArraySize(&arr, c->type);

  return ptr + size;
}
```
