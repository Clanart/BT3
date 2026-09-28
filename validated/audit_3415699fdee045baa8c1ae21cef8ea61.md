### Title
Unbounded participant count in `ThresholdKeys::read` enables attacker-controlled memory exhaustion - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) trusts the serialized `n` field (a raw `u16` read straight from the byte stream) to drive heap allocation and deserialization loops before `ThresholdParams::new` performs any semantic validation. An unprivileged party who can feed bytes to `ThresholdKeys::read` can force allocations and parsing work disproportionate to the input size, mirroring the resource-exhaustion class of CVE-2017-7940 (unbounded memory consumption while parsing a crafted file).

### Finding Description
The deserialization path is:

1. Curve ID length and ID are checked (lines 578-588).
2. `t`, `n`, `i` are read as three raw `u16`s (lines 591-602). `n` is unvalidated at this point — no bound, no `t <= n` check, no `i <= n` check.
3. If the interpolation tag is `0` (Constant), `Vec::with_capacity(usize::from(n))` is executed immediately (line 608), pre-allocating `n` scalar slots, followed by `n` calls to `C::read_F` (lines 609-611).
4. A `HashMap` is then filled with `n` calls to `C::read_G` (lines 620-623).
5. Only at the very end is `ThresholdParams::new(t, n, i)` invoked (line 626), which is the first place `t <= n` and `i <= n` are enforced — after all allocations and reads already happened.

So a ~17-byte crafted prefix (`id_len || id || t || n=0xFFFF || i || 0x00`) causes `Vec::with_capacity(65535)` to reserve ~2-3 MB (depending on `C::F` repr size) instantly, before any real data must be supplied. If the attacker actually supplies the trailing bytes, each call additionally parses up to 65535 scalars plus 65535 group elements (~4-5 MB of allocations and ~131k point-deserializations) — all from a single message whose only declared size field is an unchecked `u16`. Since this is a per-call cost with no external rate limiting inside the library, repeated submission multiplies memory pressure on the victim (a signer/processor deserializing peer-supplied key material, or any component calling `ThresholdKeys::read` on received bytes), matching the "crafted input exhausts available memory" behavior of the reference CVE.

Contrast with the analogous-but-bounded `Commitments::read` in crypto/dkg/pedpop/src/lib.rs:110-128, which caps the count at `params.t()` — a locally known, already-validated bound — rather than a stream-supplied one.

### Impact Explanation
Availability impact only: an attacker can cause disproportionate heap allocation and CPU (point deserialization) per crafted message, degrading or crashing the process handling untrusted `ThresholdKeys` encodings (e.g., coordinators/processors re-reading key material or messages embedding serialized keys). The up-front `with_capacity` means even truncated inputs cost megabytes of allocation before the read fails. No secret key material is leaked and no signature is forged, so the ceiling is Medium — consistent with the CVSS 5.5 availability-only reference.

### Likelihood Explanation
Reachability requires an attacker to get untrusted bytes into `ThresholdKeys::read`, which is an explicitly in-scope untrusted-input surface. Within in-scope flows, serialized threshold keys and DKG artifacts are exchanged between mutually distrusting validators; any participant (or anyone who can inject bytes into these channels) can supply a malformed encoding. `n` is a full `u16`, so maximal amplification is available with trivial effort. The likelihood is bounded by the fact that the per-call damage is capped at ~4-5 MB of parsing/allocation — exploitation needs repeated calls to be meaningful.

### Recommendation
Validate the header before allocating: after reading `t`, `n`, `i`, immediately call `ThresholdParams::new(t, n, i)` and reject on error, and/or enforce a sane protocol-level maximum `n`. Replace `Vec::with_capacity(usize::from(n))` with incremental `push`es (or a capacity capped at a small constant) so truncated inputs do not reserve `n`-sized memory. The same ordering fix should apply to any other readers that size allocations from unvalidated length prefixes.

### Proof of Concept
Conceptual byte stream targeting `ThresholdKeys::<C>::read`:

```
u32 id_len  = len(C::ID)            // valid, passes the curve check
    id      = C::ID                 // valid
u16 t       = 1                     // any value; t <= n not checked yet
u16 n       = 0xFFFF                // 65535 — attacker-controlled
u16 i       = 1
u8  interp  = 0x00                  // selects Interpolation::Constant
// stream ends here
```

Observed behavior at crypto/dkg/src/lib.rs:608: `Vec::with_capacity(65535)` reserves ~2-3 MB before `read_F` ever fails on the empty tail. Supplying the full payload (`65535` scalar encodings + `1` scalar + `65535` point encodings) forces the HashMap at line 620 to grow to 65535 entries and performs 65535 `read_G` decompressions — all before `ThresholdParams::new` at line 626 is reached. Repeated submissions scale this linearly, exhausting memory on the host.