### Title
Ignored parse failure (NULL) return signal in `upb_DecodeFast_SingleVarint` allows a malformed varint to be treated as a successful parse - (File: `upb/wire/decode_fast/field_varint.c`)

### Summary
`upb_DecodeFast_SingleVarint` calls `upb_WireReader_ReadVarint`, which signals malformed/truncated input by returning `NULL`, but the function never checks for that failure before using the (possibly garbage) decoded value and unconditionally returning `true` (success) with `*ptr` set to `NULL`.

### Finding Description
`upb_DecodeFast_SingleVarint` is the fast-path scalar/oneof/repeated-element varint decoder used by upb's generated "fasttable" dispatch (`upb_DecodeFast_Varint` → `upb_DecodeFast_Unpacked`) for `bool`, `int32/64`, and `zigzag` fields: [1](#0-0) 

`p = upb_WireReader_ReadVarint(p, &val, EPS(d));` returns `NULL` on a malformed varint (e.g. more than 10 continuation bytes or running off the end without a terminating byte) — exactly the same failure signal that every *other* call site of this reader in the same file checks: [2](#0-1) [3](#0-2) [4](#0-3) 

But `upb_DecodeFast_SingleVarint` skips this check entirely: it proceeds to run the zigzag/bool conversion on `val`, `memcpy`s it into `dst`, sets `*ptr = p` (which is `NULL` on failure), and unconditionally `return true` — i.e. it discards the "return value" (the NULL sentinel) of the external call and reports success regardless.

This function is used as the `single` callback in `upb_DecodeFast_Unpacked`: [5](#0-4) 

Because `single(...)` incorrectly returns `true`, the caller trusts it: `if (!single(d, &p, dst, type, ret, ctx)) return false; *ptr = p;` propagates the `NULL` pointer forward as if it were a valid parse position, and (for the repeated-field loop) continues into `upb_DecodeFast_TryMatchTag(d, p, ...)` and further tag-dispatch/`_upb_FastDecoder_LoadTag` calls that dereference `ptr` directly via `memcpy(&tag, ptr, 2)`: [6](#0-5) 

The invariant that fails to transfer/hold here is the same one flagged in the external report: an external call's return/status value (here, the `NULL` failure sentinel from `upb_WireReader_ReadVarint`) must be checked before being trusted or propagated; this code checks it everywhere else in the file except this one path.

### Impact Explanation
An attacker sending a bounded, syntactically malformed Protobuf message (a truncated/overlong varint for a `bool`/`int32`/`int64`/`sint32`/`sint64` field) through any public binary-parse API backed by upb's fast decoder (Python, PHP, Ruby upb bindings, and any C/C++ consumer using `upb_Decode`) can cause the decoder to silently accept a corrupted decode step and subsequently dereference a `NULL` pointer as the current parse position. This is reachable purely through the field-level scalar/oneof or repeated-varint fast path, not through packed fields (which do check the return value). The direct consequence is a crash (NULL-pointer dereference) in the parsing library, and in the scalar case, a corrupted/undefined field value written into the message before the crash. This is a parser integrity/availability bug on a widely used, security-relevant code path (bounded, attacker-controlled binary input parsed by a public API), analogous in class to the original "ignored external call return value" finding: a status signal is dropped and the caller proceeds as if the operation succeeded.

### Likelihood Explanation
High likelihood of triggering with a small, well-formed-looking payload: any message field of a supported varint fast-path type with a malformed varint byte sequence (either a run of ≥10 continuation bytes, or a varint bytes that run off before EOF) exercises `upb_DecodeFast_SingleVarint` on the fast decode path whenever a MiniTable field slot is fast-tabled for that field. No special registry, schema corruption, or privileged access is required — only a supported public parse entry point and a bounded malformed binary payload.

### Recommendation
In `upb_DecodeFast_SingleVarint` (`upb/wire/decode_fast/field_varint.c:53-85`), check the return of `upb_WireReader_ReadVarint` for `NULL` before using `val` or `p`, mirroring the pattern already used at lines 138, 220, and 247 of the same file:
```c
p = upb_WireReader_ReadVarint(p, &val, EPS(d));
if (UPB_UNLIKELY(!p)) {
  return UPB_DECODEFAST_ERROR(d, kUpb_DecodeStatus_Malformed, next);
}
```

### Proof of Concept
Not run in this environment (read-only analysis); reproduction requires building upb and constructing a message with a fast-table-eligible varint field followed by a 10+ byte continuation-bit-set sequence with no terminator, e.g. serialize a proto message with a single `int64` field whose wire bytes are `tag_byte 0xFF 0xFF 0xFF 0xFF 0xFF 0xFF 0xFF 0xFF 0xFF 0xFF` (10 continuation bytes, no terminator) and feed it to `upb_Decode()`/the language-binding `ParseFromString` equivalent; expected upstream behavior is a clean `kUpb_DecodeStatus_Malformed` error, but the missing check in `upb_DecodeFast_SingleVarint` causes `true`/success to be returned with `*ptr == NULL`, which callers then dereference. Confirming the crash with a debugger/ASan trace would be the next verification step for a background engineer.

### Citations

**File:** upb/wire/decode_fast/field_varint.c (L53-85)
```c
static bool upb_DecodeFast_SingleVarint(upb_Decoder* d, const char** ptr,
                                        void* dst, upb_DecodeFast_Type type,
                                        upb_DecodeFastNext* next, void* ctx) {
  UPB_ASSERT(dst);
  UPB_UNUSED(ctx);

  const char* p = *ptr;
  uint64_t val;

  p = upb_WireReader_ReadVarint(p, &val, EPS(d));

  switch (type) {
    case kUpb_DecodeFast_Bool:
      val = val != 0;
      break;
    case kUpb_DecodeFast_ZigZag32: {
      uint32_t n = val;
      val = (n >> 1) ^ -(int32_t)(n & 1);
      break;
    }
    case kUpb_DecodeFast_ZigZag64: {
      val = (val >> 1) ^ -(int64_t)(val & 1);
      break;
    }
    default:
      break;
  }

  UPB_ASSERT(upb_IsLittleEndian());
  memcpy(dst, &val, upb_DecodeFast_ValueBytes(type));
  *ptr = p;
  return true;
}
```

**File:** upb/wire/decode_fast/field_varint.c (L134-141)
```c
  if (c->type == kUpb_DecodeFast_ClosedEnum) {
    while (!upb_EpsCopyInputStream_IsDone(&c->decoder->input, &ptr)) {
      uint64_t val;
      ptr = upb_WireReader_ReadVarint(ptr, &val, &c->decoder->input);
      if (UPB_UNLIKELY(!ptr)) {
        UPB_DECODEFAST_ERROR(c->decoder, kUpb_DecodeStatus_Malformed, c->ret);
        return NULL;
      }
```

**File:** upb/wire/decode_fast/field_varint.c (L217-223)
```c
    do {
      uint64_t val;
      p = upb_WireReader_ReadVarint(p, &val, &d->input);
      if (UPB_UNLIKELY(!p)) {
        UPB_DECODEFAST_ERROR(d, kUpb_DecodeStatus_Malformed, ret);
        return;
      }
```

**File:** upb/wire/decode_fast/field_varint.c (L245-250)
```c
    uint64_t val;
    p = upb_WireReader_ReadVarint(p, &val, &d->input);
    if (UPB_UNLIKELY(!p)) {
      UPB_DECODEFAST_ERROR(d, kUpb_DecodeStatus_Malformed, ret);
      return;
    }
```

**File:** upb/wire/decode_fast/cardinality.h (L393-437)
```text
UPB_FORCEINLINE
bool upb_DecodeFast_Unpacked(upb_Decoder* d, const char** ptr, upb_Message* msg,
                             uint64_t* data, uint64_t* hasbits,
                             upb_DecodeFastNext* ret, upb_DecodeFast_Type type,
                             upb_DecodeFast_Cardinality card,
                             upb_DecodeFast_TagSize tagsize,
                             upb_DecodeFast_Single* single, void* ctx,
                             uint64_t data2) {
  const char* p = *ptr;
  if (!upb_DecodeFast_CheckTag(&p, type, card, tagsize, data, data2,
                               kUpb_DecodeFastNext_TailCallPacked, ret)) {
    return false;
  }

  void* dst;

  if (upb_DecodeFast_GetScalarField(d, p, msg, *data, hasbits, ret, &dst, card,
                                    type)) {
    if (!single(d, &p, dst, type, ret, ctx)) return false;
    *ptr = p;
    _upb_Decoder_Trace(d, 'F');
    return true;
  }

  upb_DecodeFastArray arr;
  if (!upb_DecodeFast_GetArrayForAppend(d, *ptr, msg, *data, hasbits, &arr,
                                        type, 1, ret)) {
    return false;
  }

  bool next_tag_matches;
  do {
    if (!single(d, &p, arr.dst, type, ret, ctx)) {
      upb_DecodeFastField_SetArraySize(&arr, type);
      return false;
    }
    *ptr = p;
    _upb_Decoder_Trace(d, 'F');
    next_tag_matches =
        upb_DecodeFast_TryMatchTag(d, p, arr.expected_tag, ret, tagsize);
  } while (upb_DecodeFast_NextRepeated(d, &p, ret, &arr, next_tag_matches, type,
                                       tagsize));

  return true;
}
```

**File:** upb/wire/decode_fast/dispatch.h (L39-43)
```text
UPB_INLINE uint32_t _upb_FastDecoder_LoadTag(const char* ptr) {
  uint16_t tag;
  memcpy(&tag, ptr, 2);
  return tag;
}
```
