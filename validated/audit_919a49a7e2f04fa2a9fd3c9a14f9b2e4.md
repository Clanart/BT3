### Title
Deferred bounds/limit validation in `upb_EpsCopyInputStream_ReadStringEphemeral` allows garbage patch-buffer bytes to be exposed before the error is caught - (File: `upb/wire/internal/eps_copy_input_stream.h`)

### Summary
The Sherlock report's core failure pattern is: an operation is performed optimistically without the proper validation being enforced *at the call site*, and the correctness check is only applied *afterward*, at a separate step. Between the optimistic operation and the deferred check, incorrect/unintended results (in Sherlock's case, financial state) can already have observable effects. The closest structural analog in this checkout is `upb`'s "ephemeral" string read path, which explicitly documents that it skips the size/limit check at the read site and defers detection of the error to a later, separate `upb_EpsCopyInputStream_IsDone()` call — during which window a `upb_StringView` can already contain bytes that do not correspond to real input.

### Finding Description
`upb_EpsCopyInputStream_ReadStringEphemeral()` reads a length-delimited value using a caller-supplied `size` and constructs an `upb_StringView` pointing directly at `ptr` for `size` bytes: [1](#0-0) 

The function's own comment documents the deferred-validation design explicitly: [2](#0-1) 

Specifically: "this function does not check that `size` is within the current limit or even the end of the stream... the returned data may contain garbage bytes from the patch buffer... This error will be detected later, when calling `upb_EpsCopyInputStream_IsDone()`... but it may result in nonsense bytes ending up in the output."

This mirrors the Sherlock pattern precisely:
- The "operation" (constructing the `upb_StringView` from `ptr`/`size`) is performed without the caller enforcing the true bound (the field's/message's `limit`) at the point of the read — the check against `e->limit_ptr`/`limit` that establishes correctness is applied *after*, via a separate `IsDone()` call, not integrated into the read itself.
- Because the underlying storage is the shared `patch[]` buffer (`kUpb_EpsCopyInputStream_SlopBytes * 2` bytes, reused across parse calls, per `upb/wire/internal/eps_copy_input_stream.h` lines 39-54), an ephemeral view constructed with an over-large `size` can span into the slop/patch bytes, which are unspecified/stale content rather than attacker-supplied bytes.
- The deferred `IsDone()` check is only meaningful if the caller subsequently and unconditionally calls it before consuming/propagating the ephemeral view. If any code path uses the ephemeral view's data (e.g., copies it, hashes it, compares it, or emits it as unknown/lazy field bytes) prior to reaching that `IsDone()` checkpoint, the "nonsense bytes" are already observable/propagated — exactly analogous to how the audited protocol allowed a Uniswap operation to complete and its result to be used before the post-hoc slippage check reverted.

### Impact Explanation
If a call site treats the ephemeral view as valid immediately (rather than strictly deferring all use until after `IsDone()` succeeds), stale patch-buffer content (leftover bytes from a previous field/message parsed earlier in the same buffer, not attacker-controlled plaintext) could leak into: a copied string/bytes field, an unknown-field capture, or a lazily-parsed sub-message byte range. This is a data-integrity/potential information-disclosure concern (exposure of memory that isn't part of the logical input) rather than a memory-safety violation, since reads are documented to stay "safe to read ephemerally" (within the patch buffer bounds). It does not, by itself, grant out-of-bounds memory access or RCE — the API's own documentation frames this as an internal invariant to be carefully paired with a mandatory subsequent `IsDone()` check by every caller.

### Likelihood Explanation
This function is explicitly marked internal/`ephemeral`-use only, and its documented contract requires the caller to always follow up with `upb_EpsCopyInputStream_IsDone()` before treating the result as final. I was not able to fully trace, within the remaining time/tool budget, every call site of `ReadStringEphemeral` in `upb/wire/decode.c` to confirm whether any of the 2 call sites there fail to perform the mandated `IsDone()` check before consuming the returned view (e.g., before appending it into an unknown-field buffer or handing it to an application callback). This is the key open question: the pattern (optimistic read + deferred correctness check) matches the Sherlock report's failed invariant, but whether a concrete caller violates the documented calling convention (and thus reproduces the impact) is unverified.

### Recommendation
- Audit every call site of `upb_EpsCopyInputStream_ReadStringEphemeral` (currently in `upb/wire/decode.c`) to guarantee the returned `upb_StringView` is never copied, hashed, compared, or otherwise consumed until *after* a subsequent `upb_EpsCopyInputStream_IsDone()` (or equivalent limit check) has been confirmed to succeed for that same region.
- Where feasible, prefer `upb_EpsCopyInputStream_ReadStringAlwaysAlias`, which does perform the size-vs-limit check inline (`upb/wire/internal/eps_copy_input_stream.h` lines 249-269), over the ephemeral variant, unless the deferred-check pattern is strictly necessary for performance and can be proven correct at every call site.
- Add defensive zeroing/poisoning of the patch buffer between uses (already partially done via `memset` at init, `upb/wire/internal/eps_copy_input_stream.h` line 70) so that any missed-check code path exposes zeros rather than genuinely stale memory content.

### Proof of Concept
A conclusive, executable PoC was not completed within the available tool budget. To construct one, a background engineer should:
1. Identify which of the two `upb/wire/decode.c` call sites of `ReadStringEphemeral` process a field type where the ephemeral view could be consumed (e.g., appended to an unknown-field record, or passed through a lazy/weak message path) prior to reaching the `IsDone()` check for that limit.
2. Craft a minimal serialized message with a trusted schema where a length-delimited field's declared size straddles the boundary between real input and the `kUpb_EpsCopyInputStream_SlopBytes` patch region (per the documented "*" bytes diagram in `src/google/protobuf/parse_context.h` lines 87-106, which describes the same eps-copy invariant used by `upb`).
3. Assert (via a debug build with patch-buffer poisoning/ASan) whether any bytes from the patch buffer that do not correspond to the actual serialized input are observably copied into a persisted field (e.g., an unknown-field byte string) before the malformed-message error is raised.

Given the uncertainty about whether an actual caller violates the documented contract, this should be treated as a Medium-confidence analog pending that verification, not a confirmed exploitable vulnerability.

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

**File:** upb/wire/eps_copy_input_stream.h (L109-125)
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
```
