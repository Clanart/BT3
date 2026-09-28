### Title
`ThresholdKeys::read` accepts maliciously crafted state: secret share is never bound to the verification shares, yielding a group key an attacker fully controls - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::<C>::read` deserializes `(t, n, i)`, an `Interpolation`, a `secret_share`, and `n` verification shares from raw bytes, then constructs the key set via `ThresholdKeys::new`. `ThresholdKeys::new` checks participant indexes and the interpolation method's applicability, but never verifies the fundamental consistency invariant of the structure: that `verification_shares[params.i()] == C::generator() * secret_share`, nor that the verification shares are mutually consistent/non-identity. Analogous to CVE-2018-6331 (maliciously crafted serialized state loaded and trusted), a crafted `ThresholdKeys` blob is adopted wholesale as the node's signing identity.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reads `n` arbitrary points into `verification_shares` via `<C as Ciphersuite>::read_G`, which performs canonical-encoding checks but does not reject the identity point (identity rejection only exists in `frost::Curve::read_G`, crypto/frost/src/curve/mod.rs:125-131). It then calls `ThresholdKeys::new` (lines 349-391), which:

- verifies only `verification_shares.len() == n` and `participant <= n` (lines 355-365),
- computes `group_key` as the Lagrange/Constant interpolation of `verification_shares[1..=t]` (lines 376-378),
- stores `secret_share` and `verification_shares` as-is, with no check that `secret_share` is the discrete log of `verification_shares[i]`, and no check that any share or the resulting `group_key` is non-identity.

Consequently an attacker who supplies the serialized bytes can pick `n` scalars `s_1..s_n` they know, encode `verification_shares[j] = s_j·G`, and `secret_share = s_i`. The resulting `ThresholdKeys` is internally coherent: `view()` interpolates `secret_share` correctly, FROST `sign`/`complete` verify shares against the attacker-chosen verification shares, and every produced signature validates under `group_key` — whose discrete logarithm `a_0 = Σ λ_j s_j` is known to the attacker. An identity encoding is also accepted by `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101), so shares can additionally be set to identity.

### Impact Explanation
This is the Serai analog of loading maliciously crafted serialized state: a deserialized `ThresholdKeys` is trusted as the node's threshold identity for all subsequent `PreprocessMachine`/`SignMachine`/`SignatureMachine` operations. A crafted blob yields a "threshold wallet key" the attacker unilaterally controls — every Schnorr signature the node produces under it is valid for a key whose secret the attacker knows, so any assets/authorizations bound to that group key (e.g., a Bitcoin wallet key imported into `bitcoin-serai` flow, a validator-set key) are fully compromised rather than threshold-protected. Alternatively, inconsistent `secret_share` vs. `verification_shares[i]` silently desynchronizes the node from the real validator set, causing it to sign under a key distinct from the intended one (concrete signing under an unintended group key).

### Likelihood Explanation
Exploitation requires untrusted bytes to reach `ThresholdKeys::read` — i.e., attacker influence over serialized key state (state/cache poisoning, cross-checkpoint key import, restore flows), exactly the trust assumption violated in the reference CVE where serialized cache state was loaded unsafely. Within the crypto library contract, `read` is the state-deserialization boundary and performs no semantic validation beyond syntactic canonicity, so any such channel suffices. No threshold collusion or protocol participation is needed.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), enforce structural validity of deserialized state:

- Reject identity points in verification shares and a zero `secret_share` (use the identity-rejecting read path / explicit `is_identity` checks).
- Verify `C::generator() * secret_share == verification_shares[&params.i()]` before accepting the key set.
- Reject an identity `group_key`.

### Proof of Concept
```rust
// Attacker crafts bytes for ThresholdKeys::<C>::read with t=2, n=2, i=1,
// Interpolation::Lagrange. They pick known scalars s1, s2.
let s1 = <C as Ciphersuite>::F::random(&mut rng); // known to attacker
let s2 = <C as Ciphersuite>::F::random(&mut rng);

let mut blob = vec![];
blob.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
blob.extend(C::ID);
blob.extend(2u16.to_le_bytes());          // t
blob.extend(2u16.to_le_bytes());          // n
blob.extend(1u16.to_le_bytes());          // i = Participant(1)
blob.push(1);                             // Interpolation::Lagrange
blob.extend(s1.to_repr().as_ref());       // secret_share = s1 (matches share 1)
blob.extend((C::generator() * s1).to_bytes().as_ref()); // verification_shares[1]
blob.extend((C::generator() * s2).to_bytes().as_ref()); // verification_shares[2]

// Accepted: no consistency/identity checks fail.
let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap();

// group_key interpolates to (s1*L1(0) + s2*L2(0)) * G — discrete log known to
// attacker: for included=[1,2], lambda_1 = 2, lambda_2 = -1, so secret = 2*s1 - s2.
let group_secret = (s1 * <C as Ciphersuite>::F::from(2)) - s2;
assert_eq!(keys.group_key(), C::generator() * group_secret);

// All FROST signatures produced with `keys` are valid under keys.group_key(),
// which the attacker can sign for alone — the threshold provides no protection.
// A variant with secret_share = s3 (s3 != s1) is equally accepted, silently
// desynchronizing the node from the real validator set.
```

Relevant code: `ThresholdKeys::read` crypto/dkg/src/lib.rs:574-632; `ThresholdKeys::new` crypto/dkg/src/lib.rs:349-391; `Ciphersuite::read_G` crypto/ciphersuite/src/lib.rs:91-101.