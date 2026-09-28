### Title
Attacker-controlled allocation amplification in `ThresholdKeys::read` via `Interpolation::Constant` count (memory-exhaustion DoS) - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` trusts the serialized `n` field (a `u16`) and performs `Vec::with_capacity(usize::from(n))` for `Interpolation::Constant` coefficients before validating `n` or reading any of the declared elements. This is the Serai analog of the WeeChat decompression bomb class: a small untrusted byte string causes the parser to allocate memory far out of proportion to the input size.

### Finding Description
In `ThresholdKeys::read` (`crypto/dkg/src/lib.rs`):

```rust
// crypto/dkg/src/lib.rs:606-613
let interpolation = match interpolation[0] {
  0 => Interpolation::Constant({
    let mut res = Vec::with_capacity(usize::from(n));
    for _ in 0 .. n {
      res.push(C::read_F(reader)?);
    }
    res
  }),
```

An input of roughly 11 bytes (curve-ID length + ID + `t` + `n` + `i` + interpolation tag `0`) causes an immediate `Vec::with_capacity(65535)` allocation of `C::F` elements (~32–64 bytes each, i.e., ~2–4 MB) before a single coefficient byte is read. The declared `n` is only validated afterwards in `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:355-375`, via `ThresholdParams::new` at line 626), long after the allocation occurs. Each subsequent `C::read_F` fails at EOF, but the oversized allocation has already been made; a peer that feeds a stream of such inputs (or where reads are performed concurrently per-message, as in DKG processing where `ThresholdKeys::read` / `EncryptedMessage::read` are reachable per the accepted input surface) forces repeated multi-megabyte allocations from ~11-byte inputs — an amplification factor of ~100,000x per call.

The same pattern exists in the `verification_shares` reconstruction loop (`crypto/dkg/src/lib.rs:620-623`), which iterates `1 ..= n` before `ThresholdParams::new` rejects `t > n` / invalid `i`, and each iteration performs real group-element deserialization work on attacker-controlled bytes.