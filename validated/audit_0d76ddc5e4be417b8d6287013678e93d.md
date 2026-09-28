### Title
`ThresholdKeys::read` trusts the serialized `n` field to pre-allocate a scalar vector, enabling remote memory exhaustion - (File: crypto/dkg/src/lib.rs)

### Summary
The deserializer for `ThresholdKeys` reads the participant count `n` directly from attacker-controlled bytes and immediately calls `Vec::with_capacity(usize::from(n))` before validating parameters or confirming the input actually contains `n` elements. A 2-byte length field therefore forces allocation of up to ~2 MiB of heap per call, an asymmetric resource-consumption primitive analogous to the Bento4 `CreateAtomFromStream` memory leak (CVE-2022-3668), where parsing untrusted input causes unbounded memory growth.

### Finding Description
In `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574), `t`, `n`, and `i` are read as raw `u16`s from the stream at lines 591-602 with no validation at that point (`ThresholdParams::new` is only invoked later, at line 626). When the `interpolation` byte selects `Interpolation::Constant` (line 607), the code executes:

```rust
let mut res = Vec::with_capacity(usize::from(n));
for _ in 0 .. n {
  res.push(C::read_F(reader)?);
}
```

at lines 608-612 [1](#0-0) . The `with_capacity` allocation happens before any element is read, so a truncated input still leaves the full `n * size_of::<C::F>()` allocation live until the error propagates and the `Vec` is dropped. Additionally, the verification-shares loop at lines 620-623 reads `n` group elements into a `HashMap` keyed by `Participant`s `1..=n`, again driven purely by the serialized `n` [2](#0-1) .

`n` is a `u16`, so a single input of ~20 bytes can force a 65535-scalar (~2 MiB for 256-bit fields) pre-allocation; repeated calls — e.g., a peer feeding crafted `ThresholdKeys` encodings in a loop — amplify memory consumption far beyond the input size. The same trust-the-length pattern does not appear in `Commitments::read` (bounded by the caller-fixed `params.t()`, crypto/dkg/pedpop/src/lib.rs:111-125) or `EncryptedMessage::read` (fixed-size encryptable, crypto/dkg/pedpop/src/encryption.rs:171-177); `ThresholdKeys::read` is the reachable path where the wire format itself controls the allocation.

### Impact Explanation
An unprivileged party who can cause a node/coordinator to call `ThresholdKeys::read` on bytes they supply can force repeated multi-megabyte heap allocations at negligible bandwidth cost. Over many invocations this produces memory growth/pressure on the host — the Rust analog of the upstream memory-leak DoS — degrading or crashing the process handling key deserialization (availability loss, matching the advisory's A:L impact). No secret material is leaked.

### Likelihood Explanation
Likelihood depends on deployment: `ThresholdKeys::read` must be invoked on externally-influenced bytes (e.g., key-share transport or recovery flows). Where it is reachable, exploitation is trivial — set the `n` field to `0xffff`, send repeatedly — and requires no cryptographic capability. The impact is capped by `n`'s u16 bound (~2 MiB per call), keeping this at Medium rather than High.

### Recommendation
Do not size allocations from unvalidated wire fields. In `crypto/dkg/src/lib.rs` `ThresholdKeys::read`: (1) validate `t`, `n`, `i` via `ThresholdParams::new` before allocating or iterating, (2) replace `Vec::with_capacity(usize::from(n))` with incremental `push` (or cap `n` at a protocol maximum such as the validator-set size), and (3) for `Interpolation::Constant`, read scalars into an unreserved `Vec` so allocation is proportional to bytes actually present in the stream.

### Proof of Concept
```rust
// crypto/dkg — conceptual PoC (requires a Ciphersuite, e.g. Ristretto)
// Attacker supplies ~20 bytes; each call allocates ~65535 * 32 bytes before erroring.
fn dos_read() {
  let mut buf = vec![];
  buf.extend((serai_dkg::C::ID.len() as u32).to_le_bytes()); // curve ID len
  buf.extend(serai_dkg::C::ID);                              // curve ID
  buf.extend(1u16.to_le_bytes());                            // t
  buf.extend(u16::MAX.to_le_bytes());                        // n = 65535  <-- attacker field
  buf.extend(1u16.to_le_bytes());                            // i
  buf.push(0);                                               // Interpolation::Constant
  // Stream ends here. with_capacity(65535) already ran => ~2 MiB allocated per call.
  loop {
    let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());   // repeated => memory pressure
  }
}
```
The `read_F` loop fails on EOF, but only after `Vec::with_capacity(65535)` has already committed ~2 MiB; invoking this in a loop (or on a `Read` that trickles bytes) accumulates allocation churn proportional to request count, not input size — the memory-leak/DoS bug class of CVE-2022-3668 mapped onto Serai's deserializer.

### Citations

**File:** crypto/dkg/src/lib.rs (L607-613)
```rust
      0 => Interpolation::Constant({
        let mut res = Vec::with_capacity(usize::from(n));
        for _ in 0 .. n {
          res.push(C::read_F(reader)?);
        }
        res
      }),
```

**File:** crypto/dkg/src/lib.rs (L620-623)
```rust
    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }
```
