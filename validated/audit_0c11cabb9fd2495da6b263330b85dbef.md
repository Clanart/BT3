Based on my research, the LibTIFF report describes a buffer overflow in `setrow` within `tools/thumbnail.c` — a local command-line conversion utility, not a network-facing parsing library core. The report itself states the attack vector is local and that the affected code is "no longer supported by the maintainer." This doesn't map onto Protobuf's threat model of an ordinary client sending bounded binary Protobuf/ProtoJSON through a public parse API.

I examined the analogous fixed-size/packed-field decode paths across Protobuf's supported backends, since these are the closest structural analog to LibTIFF's per-row buffer copy (a length-derived allocation followed by a bounded memcpy):

- C++ `EpsCopyInputStream::ReadPackedFixed` in `src/google/protobuf/parse_context.h` computes `num = nbytes / sizeof(T)` and reserves exactly that many entries before `memcpy`, with slop-byte-safe buffer flipping. [1](#0-0) 
- `WireFormatLite::ReadPackedFixedSizePrimitive` validates `new_bytes == length` (rejecting misaligned sizes) before choosing a fast pre-allocated path bounded by `buffer_size`, or a slow incremental path for large/malicious lengths. [2](#0-1) 
- upb's fast packed-fixed decoder (`upb_DecodeFast_PackedFixed`) rejects `size % valbytes != 0` and calls `upb_DecodeFast_GetArrayForAppend` to size/allocate the destination array before copying, so the `memcpy` target is always sized to `size`. [3](#0-2) 
- C#'s `RepeatedField<T>.AddEntriesFrom` only takes the fast fixed-size memcpy path when `length % codec.FixedSize == 0` and `ParsingPrimitives.IsDataAvailable` confirms the buffer actually has that much data, falling back to the slow per-element loop otherwise. [4](#0-3) 

In every backend, the destination buffer/array size is derived from and matched against the attacker-controlled length (`size % valbytes`, `new_bytes != length`, `IsDataAvailable`) before any bulk copy, which is exactly the invariant that failed in LibTIFF's `setrow` (writing into a fixed-size row buffer without validating the source size/index against it). Protobuf's supported parse paths all enforce this check, so there is no reachable analog where a bounded, well-formed packed/fixed field triggers an out-of-bounds write through a public parse API.

No vulnerability found for this question.

### Citations

**File:** src/google/protobuf/parse_context.h (L1521-1561)
```text
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
}
```

**File:** src/google/protobuf/wire_format_lite.h (L1116-1160)
```text
inline bool WireFormatLite::ReadPackedFixedSizePrimitive(
    io::CodedInputStream* input, RepeatedField<CType>* values) {
  int length;
  if (!input->ReadVarintSizeAsInt(&length)) return false;
  const int old_entries = values->size();
  const int new_entries = length / static_cast<int>(sizeof(CType));
  const int new_bytes = new_entries * static_cast<int>(sizeof(CType));
  if (new_bytes != length) return false;
  // We would *like* to pre-allocate the buffer to write into (for
  // speed), but *must* avoid performing a very large allocation due
  // to a malicious user-supplied "length" above.  So we have a fast
  // path that pre-allocates when the "length" is less than a bound.
  const void* data;
  int buffer_size;
  input->GetDirectBufferPointerInline(&data, &buffer_size);
  if (new_bytes <= buffer_size) {
    // Fast-path that pre-allocates *values to the final size.
#if defined(ABSL_IS_LITTLE_ENDIAN)
    values->resize(old_entries + new_entries, 0);
    // values->mutable_data() may change after resize(), so do this after:
    void* dest = reinterpret_cast<void*>(values->mutable_data() + old_entries);
    if (!input->ReadRaw(dest, new_bytes)) {
      values->Truncate(old_entries);
      return false;
    }
#else
    values->Reserve(old_entries + new_entries);
    CType value;
    for (int i = 0; i < new_entries; ++i) {
      if (!ReadPrimitive<CType, DeclaredType>(input, &value)) return false;
      values->AddAlreadyReserved(value);
    }
#endif
  } else {
    // This is the slow-path case where "length" may be too large to
    // safely allocate.  We read as much as we can into *values
    // without pre-allocating "length" bytes.
    CType value;
    for (int i = 0; i < new_entries; ++i) {
      if (!ReadPrimitive<CType, DeclaredType>(input, &value)) return false;
      values->Add(value);
    }
  }
  return true;
}
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

**File:** csharp/src/Google.Protobuf/Collections/RepeatedField.cs (L105-140)
```csharp
            if (FieldCodec<T>.IsPackedRepeatedField(tag))
            {
                int length = ctx.ReadLength();
                if (length > 0)
                {
                    int oldLimit = SegmentedBufferHelper.PushLimit(ref ctx.state, length);

                    // If the content is fixed size then we can calculate the length
                    // of the repeated field and pre-initialize the underlying collection.
                    //
                    // Check that the supplied length doesn't exceed the underlying buffer.
                    // That prevents a malicious length from initializing a very large collection.
                    if (codec.FixedSize > 0 && length % codec.FixedSize == 0 && ParsingPrimitives.IsDataAvailable(ref ctx.state, length))
                    {
                        EnsureSize(count + (length / codec.FixedSize));

                        // if littleEndian treat array as bytes and directly copy from buffer for improved performance
                        if(TryGetArrayAsSpanPinnedUnsafe(codec, out Span<byte> span, out GCHandle handle))
                        {
                            span = span.Slice(count * codec.FixedSize);
                            Debug.Assert(span.Length >= length);
                            ParsingPrimitives.ReadPackedFieldLittleEndian(ref ctx.buffer, ref ctx.state, length, span);
                            count += length / codec.FixedSize;
                            handle.Free();
                        }
                        else
                        {
                            while (!SegmentedBufferHelper.IsReachedLimit(ref ctx.state))
                            {
                                // Only FieldCodecs with a fixed size can reach here, and they are all known
                                // types that don't allow the user to specify a custom reader action.
                                // reader action will never return null.
                                array[count++] = reader(ref ctx);
                            }
                        }
                    }
```
