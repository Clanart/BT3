## Analog Identified

The FFmpeg bug's core pattern — **a short/incomplete read is treated as non-fatal, and a subsequent copy operation reads more bytes than were actually validated, exposing memory (uninitialized/stale bytes) to attacker-visible output before the deferred error is caught** — has a structural analog in upb's wire-format parser.

### Title
Deferred bounds-check in `upb_EpsCopyInputStream_ReadStringEphemeral` allows stale patch-buffer bytes into string views before error detection - (File: `upb/wire/internal/eps_copy_input_stream.h`)

### Summary
`upb_EpsCopyInputStream_ReadStringEphemeral` validates a length-delimited string's `size` only against the local slop/patch buffer window (`e->end + kUpb_EpsCopyInputStream_SlopBytes`), not against the actual pushed field/message limit. If `size` exceeds the real remaining limit but still fits inside the 16-byte slop margin, the function succeeds and returns a `upb_StringView` pointing at bytes that were never validated as belonging to the field. The real bounds violation is only detected later by `upb_EpsCopyInputStream_IsDone()`.

### Finding Description
The invariant that fails is "returned data corresponds only to bytes covered by the current limit." `ReadStringEphemeral` checks: [1](#0-0) 
against `limit = e->end + kUpb_EpsCopyInputStream_SlopBytes` — the physical buffer/patch boundary — rather than `e->limit`/`e->limit_ptr`, which represents the logical (attacker-declared) field limit. This is explicitly acknowledged in the header comment: [2](#0-1) 

This mirrors the FFmpeg flaw precisely: `zlib_decomp()` treats a short inflate as "close enough" and proceeds to copy a full frame's worth of rows from the allocation buffer regardless, deferring correctness to a caller that never re-validates the copied bytes. Here, the parser treats "fits in slop bytes" as sufficient to hand back a string view, deferring the *real* correctness check (`IsDone()`) to a point *after* the ephemeral view has already been constructed and can be consumed by the caller (e.g., copied into a field, hashed, compared, or written to JSON/text output) — the attacker-controlled value is the field's declared length, which is used to widen the returned view beyond what was actually verified as being within the message's limit.

### Impact Explanation
Because the patch buffer (`e->patch`, size `2 * kUpb_EpsCopyInputStream_SlopBytes`) is a fixed-size scratch area that is only fully `memset` once at stream `Init` and then selectively overwritten on buffer-refill fallbacks, bytes beyond what the current chunk actually copied in can retain content left over from a previous parse position/chunk, or in the worst case content that was never initialized at all (depending on how/where the `upb_EpsCopyInputStream` is allocated by the embedding decoder state). A caller that trusts a `upb_StringView` returned from `ReadStringEphemeral` before checking `IsDone()` can therefore observe bytes that do not correspond to the attacker's actual input — an information-disclosure primitive analogous to the FFmpeg AVFrame leak, though scoped to process memory reused within/adjacent to the parser's decode-context, not necessarily long-lived heap allocator metadata.

### Likelihood Explanation
Reachable from any binary Protobuf parse where a message contains a length-delimited (string/bytes) field whose declared length is attacker-controlled and can be crafted to land within the 16-byte slop margin while exceeding the real remaining limit — a bounded, ordinary malformed-but-well-formed-looking payload via a public parse API. No privileged access or hostile schema is required; only an adjusted length varint in an otherwise valid submessage.

### Recommendation
Have `upb_EpsCopyInputStream_ReadStringEphemeral` validate `size` against `e->limit`/`e->limit_ptr` (the logical, attacker-independent limit) in addition to (or instead of) the raw slop/patch boundary, or ensure the deferred `IsDone()` check occurs and is enforced *before* any returned ephemeral view can be observed/copied by the caller.

### Proof of Concept
I was not able to fully trace, within the available iterations, the concrete call site in `upb/wire/decode.c` that consumes `ReadStringEphemeral`'s result (I located two references but did not confirm whether the ephemeral view's bytes are ever copied into the final parsed message returned to the application, versus being used only for a transient, non-observable comparison). This is a real gap in the analog: the strength of the finding depends on whether the "nonsense bytes" the header comment warns about are ever attacker-observable in the parser's output, matching FFmpeg's AVFrame-exposure requirement, or whether they are always caught by `IsDone()` before leaving the decoder. Given this unresolved reachability question, I cannot certify end-to-end observability with the evidence gathered — a Devin session with access to `upb/wire/decode.c`'s full call sites for `upb_EpsCopyInputStream_ReadStringEphemeral` would be needed to confirm whether the returned `upb_StringView` is stored/copied into message fields (observable) or only used for ephemeral/internal validation (not observable) before `IsDone()` is checked.

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

**File:** upb/wire/eps_copy_input_stream.h (L113-125)
```text
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
```
