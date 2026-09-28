### Title
Attacker-controlled participant count in `ThresholdKeys::read` causes allocation before parameter validation - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::<C>::read` trusts the serialized `n` field (a `u16` fully controlled by whoever supplies the byte stream) and immediately performs `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` coefficient vector — before `ThresholdParams::new` is called to validate `t`/`n`/`i`, and before a single scalar byte has been confirmed present. A short, malformed key blob therefore forces a multi-megabyte heap allocation that is only released after the subsequent `read_F` calls fail. This is the same class as CVE-2026-54428 / GHSA-v3jc-474w-2wm6: allocation of resources proportional to an attacker-declared count before the limit/validation that would bound it is applied.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` parses `t`, `n`, `i` as raw `u16`s, then branches on the interpolation tag. For tag `0` (`Interpolation::Constant`) it executes `Vec::with_capacity(usize::from(n))` at line 608 and only afterwards loops `C::read_F(reader)?` `n` times. Parameter validation (`ThresholdParams::new(t, n, i)`, which rejects `t == 0`, `n == 0`, `t > n`, `i > n`) does not run until line 626 — after the allocation and after `n` field elements plus `n` verification-share group elements have been read (lines 618–623 also iterate `1 ..= n` performing `read_G`).

Concretely, an input consisting of a valid curve ID, `t = 0`, `n = 0xFFFF`, `i = 1`, and interpolation byte `0` — roughly 20 bytes total — triggers an immediate `Vec::with_capacity(65535)` of field elements (~2 MiB for a 32-byte field) plus entry into a loop issuing 65,535 scalar reads and then 65,535 point decodings against `read_G`. The allocation happens up front regardless of how few bytes actually follow; the read loop then burns CPU attempting reads until EOF.

### Impact Explanation
`ThresholdKeys::read` is on the list of entry points reachable with untrusted bytes. An unprivileged party that can cause a node/processor to deserialize a `ThresholdKeys` blob (e.g., via a malformed key share payload) obtains a memory-amplification primitive: ~20 bytes of input forces a ~2 MiB allocation per call, with no limit applied before the allocation. Repeated invocation amplifies allocator pressure and can exhaust memory or degrade the host — a denial of service (CWE-400) with no authentication required, directly mirroring the HPACK decoder's pre-SETTINGS-ACK unbounded header list.

### Likelihood Explanation
Reachability depends on an integrator feeding attacker-influenced bytes into `ThresholdKeys::read`; the crate exposes it as a public deserialization API with no documented requirement that the input be trusted or length-bounded beforehand. The amplification per call is moderate (bounded by `u16::MAX` elements rather than truly unbounded), and exploitation requires repeated calls or a memory-constrained target, so this is a Medium-severity DoS rather than a critical one.

### Recommendation
Validate `t`, `n`, `i` via `ThresholdParams::new` *before* any allocation or per-element reads, and reject `Interpolation::Constant` unless `t == n` up front (the `InapplicableInterpolation` check in `ThresholdKeys::new` already exists but runs too late). Replace `Vec::with_capacity(n)` with incremental `push`es so memory grows only as bytes are actually consumed, and/or cap `n` at a protocol-level maximum before use.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs:574-632 — ThresholdKeys::<C>::read
// Build a minimal malicious blob for a ciphersuite C (e.g. Ristretto):
//   id_len (4B) || C::ID || t=0x0000 (2B) || n=0xFFFF (2B) || i=0x0001 (2B) || interp=0x00
// ~20 bytes of input causes Vec::with_capacity(65535) of C::F at line 608
// before ThresholdParams::new validation runs at line 626.
let mut blob = vec![];
blob.extend(&u32::try_from(C::ID.len()).unwrap().to_le_bytes());
blob.extend(C::ID);
blob.extend(&0u16.to_le_bytes());      // t = 0 (invalid, but checked too late)
blob.extend(&u16::MAX.to_le_bytes());  // n = 65535
blob.extend(&1u16.to_le_bytes());      // i = 1
blob.push(0);                          // Interpolation::Constant
// No further bytes: read_F fails, but the ~2 MiB allocation already occurred.
let _ = ThresholdKeys::<C>::read(&mut blob.as_slice());
```

Note: I was unable to check `EncryptedMessage::read` / PedPoP deserialization paths in this iteration (the `crypto/dkg/src` directory in this index contains only `lib.rs`), so a stronger analog in the DKG message layer cannot be ruled out; the finding above stands on the confirmed code in `ThresholdKeys::read`.