### Title
`ThresholdKeys::read` accepts identity verification shares yielding an identity group key, enabling universal signature forgery - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to PyCrypto's weak ElGamal parameter generation (CVE-2018-6594), where keys were generated with parameters for which the DDH assumption fails, `ThresholdKeys::new` / `ThresholdKeys::read` in `crypto/dkg` construct and serialize-accept threshold keys with no check that the resulting `group_key` — or the per-participant `verification_shares` it is derived from — is non-identity. Any party feeding crafted bytes to `ThresholdKeys::read` can produce a `ThresholdKeys` whose `group_key()` is the identity point (discrete log 0), a degenerate "weak key" under which Schnorr/FROST signatures are trivially forgeable by anyone.

### Finding Description
`ThresholdKeys::read` reads `n` verification shares via `<C as Ciphersuite>::read_G` (`crypto/dkg/src/lib.rs:622`). `Ciphersuite::read_G` only checks canonical encoding — it does not reject the identity point (`crypto/ciphersuite/src/lib.rs:91-101`); the identity check lives only in `Curve::read_G` (`crypto/frost/src/curve/mod.rs:125-131`), which is not used here. `ThresholdKeys::new` then computes the group key as the Lagrange-weighted sum of the first `t` verification shares (`crypto/dkg/src/lib.rs:376-378`) without validating that the result is non-identity, and also never checks that the supplied `secret_share` matches its own verification share.

With e.g. `t = 1, n = 1`, a single identity verification share produces `group_key = identity`. Downstream signature verification (FROST `SignatureMachine::verify` / Schnorr verify over `view.group_key`) checks `s·G = R + c·Y`; with `Y = identity`, an attacker picks arbitrary `s`, sets `R = s·G`, and the equation holds for any challenge — a forged signature on any message.

### Impact Explanation
Any component that deserializes attacker-supplied `ThresholdKeys` (key packages, recovered/redistributed key material) and later verifies signatures against the embedded group key accepts forged signatures for arbitrary messages. If the same keys are used for signing, `view()` derives an identity `group_key` and the resulting signatures verify under a key whose discrete log is publicly known (0) — equivalent to the PyCrypto scenario where weak parameters nullify the scheme's security.

### Likelihood Explanation
Reachable by an unprivileged party through untrusted bytes passed to `ThresholdKeys::read`, which the scope explicitly lists as an attacker-controlled entry point. Exploitation requires no threshold corruption, no collusion, and no curve/hash misuse — only encoding identity points as verification shares. The cost is crafting a byte string; probability of success is 1.

### Recommendation
- In `ThresholdKeys::new`, reject if any `verification_shares` value or the computed `group_key` is identity.
- Verify `secret_share` consistency: `C::generator() * secret_share == verification_shares[params.i()]` in `ThresholdKeys::read` (or document why only callers may do so).
- Consider reading verification shares through an identity-rejecting reader (the `Curve::read_G` pattern).

### Proof of Concept
```rust
// Serialize crafted ThresholdKeys with identity verification share
let mut bytes = vec![];
bytes.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
bytes.extend(C::ID);
bytes.extend(1u16.to_le_bytes()); // t = 1
bytes.extend(1u16.to_le_bytes()); // n = 1
bytes.extend(1u16.to_le_bytes()); // i = 1
bytes.push(1);                    // Interpolation::Lagrange
bytes.extend(C::F::ZERO.to_repr().as_ref()); // secret_share = 0
bytes.extend(C::G::identity().to_bytes().as_ref()); // verification share = identity

// read_G accepts canonical identity point; ThresholdKeys::new does not check
// group_key non-identity
let keys = ThresholdKeys::<C>::read(&mut bytes.as_slice()).unwrap();
assert_eq!(keys.group_key(), C::G::identity());

// Schnorr verification for Y = identity: sG == R + c*Y reduces to sG == R
// => pick s, set R = s*G, signature verifies for any challenge/message.
```