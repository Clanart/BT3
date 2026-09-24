### Title
Potential out-of-bounds array write from mismatched varint count vs. actual writes in `upb_DecodeFast_PackedVarint` - (File: `upb/wire/decode_fast/field_varint.c`)

### Summary
The `reorder` crate's `swap_index` bug is a classic "trusted length vs. actual yield count" flaw: it reserves/allocates a buffer based on a reported `len()`, then writes elements based on the *actual* number of items an iterator yields; if those two diverge, it's an OOB write. The closest analog in this protobuf checkout is upb's fast-path packed-varint decoder, which pre-computes a varint "count" from a byte-level heuristic scan to size the array, then writes elements in a separate loop bounded only by the input stream limit — with the count/writes correspondence enforced only by a debug-only `UPB_ASSERT`, not a runtime check.

### Finding Description
In `upb_DecodeFast_PackedVarint` (`upb/wire/decode_fast/field_varint.c:108-167`), before decoding, the code computes a byte-scan estimate of how many varints are packed into the field's `size` bytes: [1](#0-0) 

This `count` is passed into `upb_DecodeFast_GetArrayForAppend`, which reserves (or newly allocates) array capacity for exactly `count` elements and computes `field->end` as the capacity boundary for the write cursor (`arr.dst`): [2](#0-1) 

The actual write loop then decodes varints one at a time and advances `arr.dst`, terminating only when `upb_EpsCopyInputStream_IsDone` (i.e., when the pushed byte-limit for this packed span is exhausted) — not when `read == count`: [3](#0-2) 

The only correspondence check between the pre-computed `count` and the actual number of writes is `UPB_ASSERT(read < count)` inside the loop, which is a debug-only assertion, compiled out in release/production builds. There is no live bounds check (e.g. `arr.dst != arr.end`) inside this varint write loop the way the unpacked/fixed-width fast path has (`upb_DecodeFast_NextRepeated` checks `field->dst == field->end`). If `upb_DecodeFast_CountVarints`'s simple byte-scan (counting non-continuation bytes) ever under-counts relative to the number of varints `upb_WireReader_ReadVarint` actually successfully decodes and consumes from the same byte span, `arr.dst` would advance past the reserved capacity (`arr.end`), writing past the allocated array buffer on the arena — a heap out-of-bounds write, structurally identical to `reorder`'s "trusted `len()` vs. actual yield count" invariant violation.

### Impact Explanation
If the count/write divergence is reachable, the impact is a heap buffer overflow inside the upb arena during parsing of any Protobuf/ProtoJSON-consuming service using the upb backend (used by Python, PHP, Ruby native extensions and directly). This could corrupt adjacent arena-allocated data, potentially leading to memory corruption reachable by an ordinary untrusted client sending a crafted packed-varint field. This would be a High-severity memory-safety defect if the mismatch is provably reachable, consistent with the reorder advisory's High/CVSS 3.1 (network, no privileges, low complexity) rating.

### Likelihood Explanation
I was **not able to prove** that `upb_DecodeFast_CountVarints`'s byte-scan can actually diverge from the number of varints decoded by `upb_WireReader_ReadVarint` for well-formed input reaching this fast path. Structurally: every valid protobuf varint terminates on exactly one byte with the continuation bit (`0x80`) clear, and `CountVarints` counts exactly those terminator bytes within `[ptr, ptr+size)`. For this to diverge, `ReadVarint` would need to either (a) consume a different number of bytes per varint than what the terminator-byte convention implies (e.g., accepting/rejecting overlong encodings differently from the scan), or (b) some byte in `[ptr, ptr+size)` is a false terminator not actually reached by sequential `ReadVarint` calls due to an early malformed-varint bailout — but a malformed varint (missing terminator within max length) causes `ReadVarint` to return `NULL`, immediately erroring out (`UPB_DECODEFAST_ERROR`) before it could overrun `count`. I could not find, within the available index, the exact overlong-varint truncation semantics of `upb_WireReader_ReadVarint`/`upb_EpsCopyInputStream_IsDone` to fully rule in or rule out a crafted "ambiguous" byte pattern where the scan and the decoder disagree (e.g., a byte sequence that is a valid terminator-byte per naive scanning but where the eps-copy input stream boundary handling — buffer patch/padding at chunk edges — causes `ReadVarint` to consume extra logical bytes it re-reads from a patch buffer, producing more successful varint reads than terminator bytes exist in the original `[ptr,ptr+size)` window). This edge case around `EpsCopyInputStream` buffer-boundary "patch" reads is the most plausible mechanism for divergence but requires verification against `upb/wire/internal/eps_copy_input_stream.h` and `upb_WireReader_ReadVarint`'s implementation, which I could not fully trace in this session.

Given this uncertainty, I present this as the strongest structural analog found, but **cannot confirm exploitability** with a concrete crafted payload from the available index.

### Recommendation
- Add a live, non-debug bounds check inside `upb_DecodeFast_PackedVarint`'s write loop (both the enum and non-enum branches) comparing `arr.dst` against `arr.end` (or `read` against `count`) before each write, falling back to the safe/slow MiniTable decoder path if exceeded — mirroring the check already present in `upb_DecodeFast_NextRepeated` for the unpacked path.
- Audit `upb_DecodeFast_CountVarints` against `upb_WireReader_ReadVarint`/`upb_EpsCopyInputStream` boundary-patch semantics to formally prove (or disprove) that the terminator-byte count always exactly matches the number of varints consumed for any input accepted by the decode loop, especially across `EpsCopyInputStream` chunk boundaries.
- Add a fuzz/conformance test that constructs packed varint payloads straddling `EpsCopyInputStream` patch-buffer boundaries with irregular varint lengths, run under ASan, to actively try to trigger `read > count`.

### Proof of Concept
Not reproduced. I identified the code path and the missing live invariant check via static reading of `upb/wire/decode_fast/field_varint.c` and `upb/wire/decode_fast/cardinality.h`, but did not have the tooling in this session to construct and run a concrete malformed/boundary-straddling packed-varint payload against the upb fast decoder to empirically demonstrate `read > count` and an actual out-of-bounds write. A background engineering session with build/test tooling (to compile upb under ASan and fuzz `upb_DecodeFast_PackedVarint` with payloads crossing `EpsCopyInputStream` buffer patch boundaries) would be required to confirm or refute this analog.

### Citations

**File:** upb/wire/decode_fast/field_varint.c (L99-106)
```c
UPB_FORCEINLINE
int upb_DecodeFast_CountVarints(const char* ptr, const char* end) {
  int count = 0;
  for (; ptr < end; ptr++) {
    count += (*ptr & 0x80) == 0;
  }
  return count;
}
```

**File:** upb/wire/decode_fast/field_varint.c (L151-162)
```c
  } else {
    int read = 0;
    while (!upb_EpsCopyInputStream_IsDone(&c->decoder->input, &ptr)) {
      UPB_ASSERT(read < count);
      if (!upb_DecodeFast_SingleVarint(c->decoder, &ptr, arr.dst, c->type,
                                       c->ret, NULL)) {
        return NULL;
      }
      arr.dst = UPB_PTR_AT(arr.dst, valbytes, char);
      ++read;
    }
  }
```

**File:** upb/wire/decode_fast/cardinality.h (L343-387)
```text
UPB_FORCEINLINE
bool upb_DecodeFast_GetArrayForAppend(upb_Decoder* d, const char* ptr,
                                      upb_Message* msg, uint64_t data,
                                      uint64_t* hasbits,
                                      upb_DecodeFastArray* field,
                                      upb_DecodeFast_Type type, int elems,
                                      upb_DecodeFastNext* next) {
  UPB_ASSERT(elems > 0);

  upb_Array** arr_p =
      UPB_PTR_AT(msg, upb_DecodeFastData_GetOffset(data), upb_Array*);
  upb_Array* arr = *arr_p;
  int lg2 = upb_DecodeFast_ValueBytesLg2(type);

  // Sync hasbits so we don't have to preserve them across the repeated field.
  upb_DecodeFast_SetHasbits(msg, *hasbits);
  *hasbits = 0;

  if (UPB_LIKELY(!arr)) {
    // upb_Array does not exist yet.  Create if with an appropriate initial
    // capacity, as long as the arena has enough size in the current block.
    int start_cap = _UPB_ARRAY_DEFAULT_INITIAL_SIZE;

    // A few arbitrary choices on the initial capacity, could be tuned later.
    while (start_cap < elems) start_cap *= 2;

    arr = UPB_PRIVATE(_upb_Array_New)(&d->arena, start_cap, lg2);
    if (!arr) {
      return UPB_DECODEFAST_ERROR(d, kUpb_DecodeStatus_OutOfMemory, next);
    }
    *arr_p = arr;
  } else if (!upb_DecodeFast_ArrayReserve(d, arr, type, elems, next)) {
    return false;
  }

  void* start = upb_Array_MutableDataPtr(arr);
  int valbytes = upb_DecodeFast_ValueBytes(type);

  field->arr = arr;
  field->dst = UPB_PTR_AT(start, upb_Array_Size(arr) * valbytes, void);
  field->end = UPB_PTR_AT(start, upb_Array_Capacity(arr) * valbytes, void);
  field->expected_tag = _upb_FastDecoder_LoadTag(ptr);

  return true;
}
```
