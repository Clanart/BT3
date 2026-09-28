### Title
Missing identity-point validation when deserializing threshold verification shares allows a group key with known discrete log - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes the `n` verification shares using `Ciphersuite::read_G`, which only enforces canonical encoding — it does not reject the identity point. `ThresholdKeys::new` likewise never checks whether any verification share (or the resulting `group_key`) is identity. This is the Serai analog of the reported "missing zero-address validation": an untrusted byte stream can instantiate `ThresholdKeys` whose group key is the identity element, i.e. a public key whose discrete logarithm (0) is known to everyone. The codebase itself demonstrates that identity points are treated as dangerous — `Curve::read_G` in FROST explicitly rejects them — yet `ThresholdKeys::read` deliberately calls `<C as Ciphersuite>::read_G` (line 622), bypassing that rejection.

### Finding Description
`Ciphersuite::read_G` performs only canonicality checks: it decodes the point via `from_bytes` and re-encodes to compare bytes, but accepts identity (`crypto/ciphersuite/src/lib.rs:91-101`). The FROST `Curve` trait wraps it with an explicit identity rejection (`crypto/frost/src/curve/mod.rs:125-131`), because identity nonces/keys are unsafe.

`ThresholdKeys::read` reads `n` verification shares with `C::read_G` and passes them to `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:620-631`). `ThresholdKeys::new` validates only the share count and that participant indexes are `<= n`, then computes `group_key` as the interpolated sum of shares `1..=t` (`crypto/dkg/src/lib.rs:349-390`). Neither function rejects identity shares, so a crafted serialization with all-identity verification shares produces `ThresholdKeys` with `group_key == identity`, and `secret_share` may also be a zero scalar read via `read_F` with no non-zero check (`crypto/dkg/src/lib.rs:618`).

### Impact Explanation
A group key equal to the identity point has discrete logarithm 0 — publicly known. Any Schnorr/FROST signature under this key is trivially forgeable: pick any `r`, set `R = r*G`, `s = r`; then `R + c*identity - s*G = 0` satisfies `SchnorrSignature::verify` (`crypto/schnorr/src/lib.rs:108-110`). Concretely:

- A victim that loads attacker-supplied `ThresholdKeys` bytes (e.g. keys restored/propagated as serialized blobs) obtains a "threshold key" whose Bitcoin/Taproot output anyone can spend, and for which arbitrary signatures verify — funds are either unspendable-by-intended-key or stealable by anyone.
- Because the forgery is algebraic, no threshold of honest participants is needed; the "multisig" provides zero security while appearing structurally valid (`t`, `n`, `i`, share counts all check out).

### Likelihood Explanation
Reachability requires untrusted bytes to reach `ThresholdKeys::read`, which is an enumerated untrusted-input sink for this audit. Whether a deployment actually feeds network-controlled bytes into it determines practical exploitability, so this is rated Medium rather than High: the cryptographic consequence (a key with universally known discrete log, trivially forgeable signatures, stealable funds) is severe, but it hinges on the deserialization path being exposed to attacker input.

### Recommendation
In `ThresholdKeys::read` (or inside `ThresholdKeys::new`), reject identity verification shares and a zero `secret_share`, and additionally assert the computed `group_key` is non-identity:

```rust
// in ThresholdKeys::new, after computing group_key
if bool::from(group_key.is_identity()) {
  Err(DkgError::IdentityGroupKey)?
}
// and per-share:
for (p, share) in &verification_shares {
  if bool::from(share.is_identity()) { Err(DkgError::IdentityVerificationShare(*p))? }
}
```

Alternatively, deserialize shares with `Curve::read_G`-equivalent identity rejection where the ciphersuite context allows.

### Proof of Concept
```rust
// Craft a ThresholdKeys serialization for curve C with all-identity verification shares
let mut bytes = vec![];
bytes.extend((C::ID.len() as u32).to_le_bytes());
bytes.extend(C::ID);
bytes.extend(1u16.to_le_bytes()); // t = 1
bytes.extend(1u16.to_le_bytes()); // n = 1
bytes.extend(1u16.to_le_bytes()); // i = 1
bytes.push(1);                    // Interpolation::Lagrange
bytes.extend(C::F::ZERO.to_repr().as_ref());        // secret_share = 0
bytes.extend(C::G::identity().to_bytes().as_ref()); // verification_shares[1] = identity

let keys = ThresholdKeys::<C>::read(&mut bytes.as_slice()).unwrap(); // accepted
assert!(bool::from(keys.group_key().is_identity()));

// Forge a signature for any challenge c:
let r = C::F::random(&mut OsRng);
let sig = SchnorrSignature::<C> { R: C::generator() * r, s: r };
assert!(sig.verify(keys.group_key(), challenge)); // R + c*0 - sG == 0
```

Relevant code: `crypto/dkg/src/lib.rs:618-631` (reads shares without identity checks), `crypto/dkg/src/lib.rs:355-378` (`ThresholdKeys::new` validates only counts/indexes), `crypto/ciphersuite/src/lib.rs:91-101` (`read_G` lacks identity rejection), `crypto/frost/src/curve/mod.rs:123-131` (the identity check that is bypassed), `crypto/schnorr/src/lib.rs:108-110` (verification satisfied under identity key).