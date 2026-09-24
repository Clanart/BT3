## Summary

The strongest analog is a missing-NULL-check pattern in upb's **text/debug-string encoder**, `upb/text/internal/encode.c`, in the function `UPB_PRIVATE(_upb_TextEncode_Unknown)`. This function decodes and pretty-prints unknown-field bytes (including a "speculative" recursive re-parse of length-delimited unknown fields as sub-messages) and is reached by `upb_DebugString()` / `TextFormat`-style printing paths in the upb-backed Python/Ruby/PHP bindings and C++ debug helpers whenever a message containing unrecognized fields is printed after being parsed from attacker-controlled binary protobuf.

## Finding Description

The GT.M/YottaDB CVE is a NULL-pointer dereference that occurs specifically in a *printing/formatting* routine (`ZPrint`) when it consumes crafted, already-accepted data whose internal structure the printer does not fully re-validate before dereferencing a pointer it computed from that data. The transferable invariant is: **a decode/read helper that can return an error/NULL pointer must have every call site check that return value before using the pointer again**, especially in a recursive "print what we already have" path that re-parses raw bytes.

In `_upb_TextEncode_Unknown` [1](#0-0) , each wire-type branch reads its payload and then loops back to `upb_EpsCopyInputStream_IsDone(stream, &ptr)` to decide whether to continue. For `kUpb_WireType_Varint` and `kUpb_WireType_Delimited`, the code wraps the read in the `CHK()` macro, which bails out (`return NULL;`) if the reader returns a NULL/error pointer: [2](#0-1) [3](#0-2) [4](#0-3) 

However, the `kUpb_WireType_32Bit` and `kUpb_WireType_64Bit` branches assign the result of `upb_WireReader_ReadFixed32`/`upb_WireReader_ReadFixed64` directly to `ptr` **without** the `CHK()` guard: [5](#0-4) 

If the underlying bytes are truncated (fewer than 4/8 bytes remaining), these readers return an error/NULL pointer just like the varint/delimited readers do — but that failure is silently ignored here, and the loop proceeds to call `upb_EpsCopyInputStream_IsDone(stream, &ptr)` and then `upb_WireReader_ReadTag(ptr, ...)` with a corrupted `ptr` value on the next iteration.

Critically, this code path is explicitly invoked for **speculative, unvalidated** re-parsing of length-delimited unknown-field payloads: when an unknown field has wire type `Delimited`, the function heuristically tries to parse the raw bytes as a nested message by recursively calling itself on a fresh sub-stream over attacker-supplied bytes that have *not* been validated as well-formed wire format: [6](#0-5) 

The top-of-function comment states an invariant — "We are guaranteed that the unknown data is valid wire format" — [7](#0-6)  — but this guarantee does not hold for the nested speculative sub-parse, since the whole point of that branch is to try interpreting arbitrary bytes as a message even though they were never validated as such. A truncated 4- or 8-byte fixed field at the tail of that speculative buffer is exactly the kind of malformed input this invariant does not cover, and it is exactly the case the missing `CHK()` fails to catch.

This is called from `UPB_PRIVATE(_upb_TextEncode_ParseUnknown)`, which is invoked by `UPB_PRIVATE(_upb_MessageDebugString)` for every message printed via `upb_DebugString` [8](#0-7) [9](#0-8) , which is the debug-string/TextFormat-equivalent printing entry point exercised in tests such as `upb/text/encode_debug_test.cc` [10](#0-9) .

## Impact Explanation

If reachable, this is a NULL-pointer dereference (crash / denial of service) triggered purely by parsing attacker-supplied binary protobuf bytes that contain a length-delimited unknown field whose contents end with a truncated fixed32/fixed64 tag, followed by printing that message (via `DebugString`/upb text encode) — a common pattern for logging, error reporting, or debugging untrusted input. No memory corruption beyond the crash, no confidentiality/integrity impact, consistent with the CVE's own scope (Availability-only, C:N/I:N/A:H).

## Likelihood Explanation

Moderate-to-high if the print path is reachable for untrusted messages: it requires (1) a message parsed from attacker bytes that contains an unrecognized field number stored as "unknown," (2) that unknown field being wire type `Delimited`, and (3) the delimited payload itself containing a byte sequence that the speculative sub-parser interprets as a tag with wire type 32-bit/64-bit but with insufficient trailing bytes. All of this is fully attacker-controlled and does not require any privileged access — only that some application path calls a debug-print/`DebugString`-equivalent function on the parsed message.

**Caveat / what I could not fully verify:** I was not able to inspect the concrete implementations of `upb_WireReader_ReadFixed32`, `upb_WireReader_ReadFixed64`, and `upb_EpsCopyInputStream_IsDone` before running out of tool iterations, so I cannot confirm with certainty (a) that these fixed-width readers actually return NULL/error pointers on insufficient data (rather than, e.g., silently reading out-of-bounds or clamping), and (b) exactly how `IsDone`/`ReadTag` behave when handed a NULL `ptr` (i.e., whether the crash manifests as a dereference inside `IsDone`, inside the next `ReadTag`, or is silently absorbed by a bounds check). The structural asymmetry — `CHK()` used for varint/delimited reads but omitted for the two fixed-width reads in the same function — is concrete and directly observable in the source, but confirming the exact crash mechanics and reproducing it would require building and running the upb text encoder against a crafted message, which I could not do in this read-only investigation.

## Recommendation

Wrap the `upb_WireReader_ReadFixed32`/`upb_WireReader_ReadFixed64` calls in `_upb_TextEncode_Unknown` with the same `CHK()` guard used for the varint and delimited cases, so that a NULL/error return immediately aborts the unknown-field printing (falling back to raw-byte printing, matching the existing failure-recovery behavior seen for the delimited case) instead of allowing a corrupted pointer to flow into the next loop iteration.

## Proof of Concept

Not executed — I could not run code in this read-only investigation. The theoretical PoC would be: construct a binary-encoded message where an unrecognized field number is emitted with wire type 2 (length-delimited) whose payload is itself a wire-format-looking sequence ending in a tag for wire type 5 (fixed32) or 1 (fixed64) followed by fewer than 4 or 8 trailing bytes; parse this into a upb-backed message via a public `ParseFrom`-equivalent API; then call `upb_DebugString` (or the corresponding Python/Ruby/PHP `__str__`/inspect path that uses the upb text encoder) on the resulting message. Confirming an actual crash would require compiling `upb/text/internal/encode.c` and running this input through `upb_DebugString`, which was outside the scope of what I could execute here.

### Citations

**File:** upb/text/internal/encode.c (L41-46)
```c
#define CHK(x)     \
  do {             \
    if (!(x)) {    \
      return NULL; \
    }              \
  } while (0)
```

**File:** upb/text/internal/encode.c (L68-156)
```c
const char* UPB_PRIVATE(_upb_TextEncode_Unknown)(txtenc* e, const char* ptr,
                                                 upb_EpsCopyInputStream* stream,
                                                 int groupnum) {
  // We are guaranteed that the unknown data is valid wire format, and will not
  // contain tag zero.
  uint32_t end_group = groupnum > 0
                           ? ((groupnum << kUpb_WireReader_WireTypeBits) |
                              kUpb_WireType_EndGroup)
                           : 0;

  while (!upb_EpsCopyInputStream_IsDone(stream, &ptr)) {
    uint32_t tag;
    CHK(ptr = upb_WireReader_ReadTag(ptr, &tag, stream));
    if (tag == end_group) return ptr;

    UPB_PRIVATE(_upb_TextEncode_Indent)(e);
    UPB_PRIVATE(_upb_TextEncode_Printf)
    (e, "%d: ", (int)upb_WireReader_GetFieldNumber(tag));

    switch (upb_WireReader_GetWireType(tag)) {
      case kUpb_WireType_Varint: {
        uint64_t val;
        CHK(ptr = upb_WireReader_ReadVarint(ptr, &val, stream));
        UPB_PRIVATE(_upb_TextEncode_Printf)(e, "%" PRIu64, val);
        break;
      }
      case kUpb_WireType_32Bit: {
        uint32_t val;
        ptr = upb_WireReader_ReadFixed32(ptr, &val, stream);
        UPB_PRIVATE(_upb_TextEncode_Printf)(e, "0x%08" PRIu32, val);
        break;
      }
      case kUpb_WireType_64Bit: {
        uint64_t val;
        ptr = upb_WireReader_ReadFixed64(ptr, &val, stream);
        UPB_PRIVATE(_upb_TextEncode_Printf)(e, "0x%016" PRIu64, val);
        break;
      }
      case kUpb_WireType_Delimited: {
        int size;
        char* start = e->ptr;
        size_t start_overflow = e->overflow;
        upb_StringView sv;
        CHK(ptr = upb_WireReader_ReadSize(ptr, &size, stream));
        CHK(ptr = upb_EpsCopyInputStream_ReadStringAlwaysAlias(stream, ptr,
                                                               size, &sv));

        // Speculatively try to parse as message.
        UPB_PRIVATE(_upb_TextEncode_PutStr)(e, "{");
        UPB_PRIVATE(_upb_TextEncode_EndField)(e);

        // EpsCopyInputStream can't back up, so create a sub-stream for the
        // speculative parse.
        upb_EpsCopyInputStream sub_stream;
        const char* sub_ptr = sv.data;
        upb_EpsCopyInputStream_Init(&sub_stream, &sub_ptr, size);

        e->indent_depth++;
        if (UPB_PRIVATE(_upb_TextEncode_Unknown)(e, sub_ptr, &sub_stream, -1)) {
          e->indent_depth--;
          UPB_PRIVATE(_upb_TextEncode_Indent)(e);
          UPB_PRIVATE(_upb_TextEncode_PutStr)(e, "}");
        } else {
          // Didn't work out, print as raw bytes.
          e->indent_depth--;
          e->ptr = start;
          e->overflow = start_overflow;
          UPB_PRIVATE(_upb_TextEncode_Bytes)(e, sv);
        }
        break;
      }
      case kUpb_WireType_StartGroup:
        UPB_PRIVATE(_upb_TextEncode_PutStr)(e, "{");
        UPB_PRIVATE(_upb_TextEncode_EndField)(e);
        e->indent_depth++;
        CHK(ptr = UPB_PRIVATE(_upb_TextEncode_Unknown)(
                e, ptr, stream, upb_WireReader_GetFieldNumber(tag)));
        e->indent_depth--;
        UPB_PRIVATE(_upb_TextEncode_Indent)(e);
        UPB_PRIVATE(_upb_TextEncode_PutStr)(e, "}");
        break;
      default:
        return NULL;
    }
    UPB_PRIVATE(_upb_TextEncode_EndField)(e);
  }

  return end_group == 0 && !upb_EpsCopyInputStream_IsError(stream) ? ptr : NULL;
}
```

**File:** upb/text/internal/encode.c (L160-191)
```c
void UPB_PRIVATE(_upb_TextEncode_ParseUnknown)(txtenc* e,
                                               const upb_Message* msg) {
  if ((e->options & UPB_TXTENC_SKIPUNKNOWN) != 0) return;

  uintptr_t iter = kUpb_Message_UnknownBegin;
  upb_MessageUnknown unknown;
  while (upb_Message_NextUnknown2(msg, &unknown, &iter)) {
    if (unknown.type == kUpb_MessageUnknownType_StringView) {
      upb_StringView view = unknown.value.bytes;
      char* start = e->ptr;
      upb_EpsCopyInputStream stream;
      upb_EpsCopyInputStream_Init(&stream, &view.data, view.size);
      if (!UPB_PRIVATE(_upb_TextEncode_Unknown)(e, view.data, &stream, -1)) {
        /* Unknown failed to parse, back up and don't print it at all. */
        e->ptr = start;
      }
    } else {
      UPB_ASSERT(unknown.type == kUpb_MessageUnknownType_NonCanonicalExtension);
      const struct upb_Extension* ext_struct = unknown.value.extension;
      const upb_MiniTableExtension* ext = ext_struct->ext;
      upb_MessageValue val_ext = ext_struct->data;
      const upb_MiniTableField* f = upb_MiniTableExtension_ToField(ext);
      const upb_MiniTable* mt = upb_MiniTableExtension_Extendee(ext);
      UPB_ASSERT(!upb_MiniTableField_IsMap(f));
      if (upb_MiniTableField_IsArray(f)) {
        _upb_ArrayDebugString(e, val_ext.array_val, f, mt, ext);
      } else {
        _upb_FieldDebugString(e, val_ext, f, mt, NULL, ext);
      }
    }
  }
}
```

**File:** upb/text/internal/encode.c (L363-404)
```c
void UPB_PRIVATE(_upb_MessageDebugString)(txtenc* e, const upb_Message* msg,
                                          const upb_MiniTable* mt) {
  size_t iter = kUpb_BaseField_Begin;
  const upb_MiniTableField* f;
  upb_MessageValue val;

  // Base fields will be printed out first, followed by extension fields, and
  // finally unknown fields.

  while (UPB_PRIVATE(_upb_Message_NextBaseField)(msg, mt, &f, &val, &iter)) {
    if (upb_MiniTableField_IsMap(f)) {
      _upb_MapDebugString(e, val.map_val, f, mt);
    } else if (upb_MiniTableField_IsArray(f)) {
      // ext set to NULL as we're not dealing with extensions yet
      _upb_ArrayDebugString(e, val.array_val, f, mt, NULL);
    } else {
      // ext set to NULL as we're not dealing with extensions yet
      // label set to NULL as we're not currently working with a MapEntry
      _upb_FieldDebugString(e, val, f, mt, NULL, NULL);
    }
  }

  const upb_MiniTableExtension* ext;
  upb_MessageValue val_ext;
  iter = kUpb_Message_ExtensionBegin;
  while (upb_Message_NextExtension(msg, &ext, &val_ext, &iter)) {
    const upb_MiniTableField* f = &ext->UPB_PRIVATE(field);
    // It is not sufficient to only pass |f| as we lose valuable information
    // about sub-messages. It is required that we pass |ext|.
    if (upb_MiniTableField_IsMap(f)) {
      UPB_UNREACHABLE();  // Maps cannot be extensions.
      break;
    } else if (upb_MiniTableField_IsArray(f)) {
      _upb_ArrayDebugString(e, val_ext.array_val, f, mt, ext);
    } else {
      // label set to NULL as we're not currently working with a MapEntry
      _upb_FieldDebugString(e, val_ext, f, mt, NULL, ext);
    }
  }

  UPB_PRIVATE(_upb_TextEncode_ParseUnknown)(e, msg);
}
```

**File:** upb/text/encode_debug_test.cc (L22-53)
```text
std::string GetDebugString(const upb_Message* input,
                           const upb_MiniTable* mt_main) {
  // Resizing/reallocation of the buffer is not necessary since we're only
  // testing that we get the expected debug string.
  char buf[100];
  int options =
      UPB_TXTENC_NOSORT;  // Does not matter, but maps will not be sorted.
  size_t real_size = upb_DebugString(input, mt_main, options, buf, 100);
  EXPECT_EQ(buf[real_size], '\0');
  return std::string(buf);
}

TEST(TextNoReflection, ExtensionsString) {
  const upb_MiniTable* mt_main = &upb_0test__ModelWithExtensions_msg_init;
  upb_Arena* arena = upb_Arena_New();

  upb_test_ModelExtension1* extension1 = upb_test_ModelExtension1_new(arena);
  upb_test_ModelExtension1_set_str(extension1,
                                   upb_StringView_FromString("Hello"));

  upb_test_ModelWithExtensions* msg = upb_test_ModelWithExtensions_new(arena);

  upb_test_ModelExtension1_set_model_ext(msg, extension1, arena);

  std::string buf = GetDebugString(UPB_UPCAST(msg), mt_main);
  upb_Arena_Free(arena);
  std::string golden = R"([1547] {
  25: "Hello"
}
)";
  ASSERT_EQ(buf, golden);
}
```
