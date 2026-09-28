### Title
Unbounded allocation in `SchnorrAggregate::read` enables memory-exhaustion DoS from untrusted bytes - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` reads an attacker-controlled `u32` element count from the input stream and then pushes that many group elements into a `Vec<C::G>` with no upper bound, enabling a remote unbounded-allocation denial of service analogous to CVE-2024-28575 (untrusted-input-driven resource exhaustion during parsing).

### Finding Description
In `crypto/schnorr/src/aggregate.rs:77-88`, deserialization proceeds as follows:

```rust
pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
  let mut len = [0; 4];
  reader.read_exact(&mut len)?;
  let mut Rs = vec![];
  for _ in 0 .. u32::from_le_bytes(len) {
    Rs.push(C::read_G(reader)?);
  }
  Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
}
```

The `len` field is fully attacker-controlled (up to ~4.29 billion). Each iteration calls `C::read_G`, which performs point decompression (`from_bytes`) plus a canonicality re-encoding check in `Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`). There is no cap on `len` and no check that `len` is consistent with the number of signatures/challenges the verifier will actually evaluate (`verify` rejects length mismatches only *after* the full allocation/parse at `aggregate.rs:128-130`).

While `read_exact` will terminate the loop early if the stream is truncated, an attacker who supplies `len = u32::MAX` together with a stream of valid (or invalid-but-parseable) point encodings forces the reader to:

- perform up to ~4 billion elliptic-curve decompressions (CPU exhaustion), and
- grow `Rs` to `u32::MAX * size_of::<C::G>()` bytes of heap (memory exhaustion / abort on OOM).

The same pattern — trusting a serialized count to size work — is how the FreeImage bug operates: a length field in attacker-controlled input drives unbounded consumption. Other in-scope readers either bound their loops by local parameters (`Commitments::read` is bounded by `generators.len()` in `crypto/frost/src/nonce.rs:133-139`; `ThresholdKeys::read` is bounded by a `u16` `n` in `crypto/dkg/src/lib.rs:608-623`), making `SchnorrAggregate::read` the uniquely unbounded, attacker-reachable path.

### Impact Explanation
Any component that deserializes an aggregate Schnorr signature from untrusted bytes (e.g., a processor/coordinator verifying signed messages over the network) can be crashed or forced to exhaust memory/CPU by a single crafted input. This is a remote, unprivileged DoS on the verification path — availability loss for the node handling the message, matching the Medium-severity DoS profile of the reference CVE.

### Likelihood Explanation
Likelihood is moderate: exploitation requires only the ability to submit serialized `SchnorrAggregate` data to a verifier — a public input — and a `u32::MAX` length prefix. No key material, collusion, or privileged position is needed. The cost is bounded only by the host's memory/CPU limits; with truncated streams the attack degrades to parse-until-EOF, but a multi-GB buffer of valid encodings still forces full allocation before verification rejects the length mismatch.

### Recommendation
Bound `len` before allocating: either precompute an upper limit (e.g., the maximum plausible number of aggregated signatures, or the length of the remaining reader if it's a `&[u8]`), or switch to a streaming design that deserializes into a capacity-checked `Vec` and rejects counts exceeding a protocol-defined maximum. Also consider performing a `Vec::with_capacity` only after the bound check to avoid large eager reservations.

### Proof of Concept
Conceptually, any caller invoking `SchnorrAggregate::<C>::read` on attacker-supplied bytes is vulnerable:

```rust
// Attacker-controlled bytes: 0xFFFFFFFF count followed by repeated valid point encodings
let mut payload = u32::MAX.to_le_bytes().to_vec();
let enc = C::generator().to_bytes();
for _ in 0 .. (1 << 20) { payload.extend(enc.as_ref()); } // ~32-64 MB of points
let _ = SchnorrAggregate::<C>::read(&mut payload.as_slice()); // allocates Vec<C::G> growth unbounded
```

The loop at `aggregate.rs:83-85` will keep pushing `C::G` elements for every supplied encoding, with memory growth linear in attacker input and no rejection until `verify` is separately invoked.