The out-of-scope coordinator code shows a classic pattern (unbounded `Vec::with_capacity` from a u32 count). Let me check the in-scope read functions for the same class.### Title
Attacker-controlled participant count in `ThresholdKeys::read` causes unbounded point-decompression work and oversized allocations - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` trusts a `u16` `n` field embedded in attacker-supplied bytes and uses it to (a) pre-allocate `Vec::with_capacity(n)` for `Interpolation::Constant` coefficients, and (b) drive a `1..=n` loop of `<C as Ciphersuite>::read_G(reader)` calls populating `verification_shares`, followed by `ThresholdKeys::new`, which interpolates over all `n` shares. This is the same class as GHSA-xg9f-g7g7-2323 (CWE-400/CWE-770): a small length field declares a part count that controls per-part CPU (point decoding) and memory (allocations) with no sanity cap relative to the actual key set.

### Finding Description
In `crypto/dkg/src/lib.rs:574-632`:

- `n` is read as a raw `u16` from the stream (lines 591-602), giving up to 65,535 declared shares.
- For `interpolation == 0`, `Vec::with_capacity(usize::from(n))` is allocated immediately (line 608), then `n` `read_F` calls are performed (lines 609-612).
- Unconditionally, a `HashMap` is filled by looping `1..=n` and calling `C::read_G(reader)` per iteration (lines 620-623). Each `read_G` performs full group-element decoding (Ristretto/ed448 decompression, validity checks) — the most expensive primitive per byte in the library.
- `ThresholdKeys::new` (line 625) then builds the group key via interpolation over the `n` verification shares, adding further O(n·t)-ish multiexp work.

No bound ties `n` to the number of bytes remaining or to a protocol maximum; `ThresholdParams::new` only checks consistency (t ≤ n, i ≤ n, nonzero), not magnitude.

### Impact Explanation
An unprivileged party able to feed bytes to `ThresholdKeys::read` (listed as an in-scope attacker input surface) can force:

- ~65,535 group-element decodings per message — seconds of CPU per ~2–4 MB input (repeating a single valid point encoding suffices; the loop doesn't require distinct points).
- Repeated concurrent submissions amplify this into worker exhaustion, matching the advisory's CPU-blocking DoS.
- With `interpolation == 0`, a 2-byte field triggers a `Vec::with_capacity(65535)` (~2 MB) allocation before a single scalar byte is read, enabling memory-pressure amplification from tiny inputs.

### Likelihood Explanation
Reachable wherever serialized `ThresholdKeys` are accepted from untrusted input (reshare/recovery/promote flows deserialize peer-provided keys via this API, e.g. `crypto/dkg/promote` and `crypto/dkg/recovery` consume `ThresholdKeys::read`). The input format is trivially constructible: valid `C::ID` length + `C::ID`, `t`, `n=0xffff`, `i`, interpolation tag, then repeated point encodings. No secret knowledge or collusion is needed. Bounded by `u16`, so Medium rather than High.

### Recommendation
Cap `n` at a protocol-level maximum (e.g. the `MAX_KEY_SHARES_PER_SET` bound used elsewhere in Serai) before allocating or looping; defer `Vec::with_capacity` until after bytes are confirmed present, or pre-size by `min(n, remaining_len / element_size)`; reject `n` values inconsistent with the local session's expected participant count.

### Proof of Concept
```rust
// For a ciphersuite C (e.g. Ristretto), craft:
let mut buf = vec![];
buf.extend(&(C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);                    // curve ID check passes
buf.extend(&1u16.to_le_bytes());      // t = 1
buf.extend(&u16::MAX.to_le_bytes());  // n = 65535  <- attacker controlled
buf.extend(&1u16.to_le_bytes());      // i = 1
buf.push(1);                          // Interpolation::Lagrange (skips scalar vec)

// A single valid encoding of C::generator(), repeated 65535 times:
let enc = C::generator().to_bytes();
for _ in 0 .. u16::MAX { buf.extend(enc.as_ref()); }

// Triggers 65535 read_G decompressions + HashMap inserts, then
// ThresholdKeys::new interpolates over 65535 verification shares.
let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());
```
With `interpolation = 0` instead of `1`, only the ~11-byte header is needed to force the `Vec::with_capacity(65535)` allocation; the subsequent `read_F` loop then fails on EOF only after the allocation is committed.