### Title
Deserialization of untrusted `ThresholdKeys` admits identity verification shares, yielding an identity group key and forgeable threshold signatures - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes the per-participant verification shares with `Ciphersuite::read_G`, which enforces canonical encoding but explicitly permits the identity point, rather than the identity-rejecting `Curve::read_G` used elsewhere in FROST. `ThresholdKeys::new` then computes `group_key` as an interpolated sum over these shares with no identity/non-degeneracy check. An attacker who can feed crafted bytes to `ThresholdKeys::read` produces keys whose group key (and individual verification shares) are the identity, collapsing the Schnorr/FROST verification equations and enabling signature forgery and partial-signature verification bypass.

### Finding Description
`ThresholdKeys::read` reads `n` verification shares via `<C as Ciphersuite>::read_G(reader)` at crypto/dkg/src/lib.rs:620-623. `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101) only rejects non-canonical encodings; it accepts the identity encoding. This contrasts with `Curve::read_G` (crypto/frost/src/curve/mod.rs:123-131), which additionally rejects identity — a check deliberately added because identity points degenerate discrete-log-based verification.

`ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) validates only the count and participant indexes of `verification_shares` (lines 355-365) and computes `group_key = Σ verification_shares[i] * interpolation_factor(i, 1..=t)` at lines 376-378. If all shares are identity, `group_key` is identity. There is no check that shares or the resulting group key are non-identity.

Downstream, `group_key()` (line 445-447) returns this identity (scaled/offset), and `view()` (lines 463-533) propagates the identity verification shares into `ThresholdView`, where they are used as the public keys in the FROST partial-signature verification equation `s_i·G == D_i + ρ·E_i + c·λ_i·Y_i`. With `Y_i = identity`, that equation reduces to `s_i·G == D_i + ρ·E_i`, satisfiable without any knowledge of the secret share.

### Impact Explanation
Two concrete impacts:

1. **Forged threshold signatures.** Any Schnorr/FROST verification performed against a `ThresholdView`/`ThresholdKeys` deserialized from attacker bytes has group key = identity. A signature `(R = s·G, s)` verifies trivially for arbitrary `s`, since `s·G == R + c·identity == R`. Whoever relies on these keys for signature verification accepts forged signatures.

2. **Partial-signature verification bypass.** An individual identity verification share for participant `j` lets anyone produce valid signature shares for `j` (choose any preprocess `D, E`, output `s_i = d + ρ·e`) without possessing a key share — defeating the per-participant accountability of the threshold scheme.

### Likelihood Explanation
Reachability requires attacker-controlled bytes reaching `ThresholdKeys::read`. The scan rules explicitly list `ThresholdKeys::read` as an in-scope sink for untrusted bytes, and the function's own design (curve-ID check at lines 578-588) shows it is intended to distinguish *which* bytes are valid rather than assuming trusted provenance. Likelihood therefore depends on the integrator's storage path: if the serialized keys are ever loaded from storage or transport an attacker can modify (e.g., restored from backup, received during a reshare/recovery flow), the attack is fully deterministic — no brute force required, just `n` identity encodings.

### Recommendation
In `ThresholdKeys::read` (crypto/dkg/src/lib.rs:620-623), use the identity-rejecting `Curve::read_G`-equivalent for the ciphersuite (or perform an explicit `is_identity` rejection) for each verification share. Additionally, in `ThresholdKeys::new`, reject identity verification shares and reject an identity `group_key` after interpolation. The same audit should be applied to other readers that use `Ciphersuite::read_G` on untrusted input where identity is not meaningful, e.g. `EncryptedMessage::read`/`EncryptionKeyMessage::read` in crypto/dkg/pedpop/src/encryption.rs:57-58,171-177 (identity `key` makes the ECDH shared point identity, yielding a publicly derivable cipher key) and `Commitments::read` in crypto/dkg/pedpop/src/lib.rs:115-127.

### Proof of Concept
```rust
// For a FROST curve C (e.g. Secp256k1 via frost::curve::Secp256k1), craft serialized
// ThresholdKeys with t = n = 2 (Lagrange), i = 1, and both verification shares = identity.
let mut buf = vec![];
buf.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
buf.extend(C::ID);
buf.extend(2u16.to_le_bytes()); // t
buf.extend(2u16.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes()); // i
buf.push(1); // Interpolation::Lagrange
buf.extend(<C as Ciphersuite>::F::ONE.to_repr().as_ref()); // secret_share (any scalar)
// identity encoding for C::G, twice
let identity_enc = C::G::identity().to_bytes();
buf.extend(identity_enc.as_ref());
buf.extend(identity_enc.as_ref());

let keys = ThresholdKeys::<C>::read::<&[u8]>(&mut buf.as_ref()).unwrap();
// group_key is the interpolated sum of identity points:
assert!(bool::from(keys.group_key().is_identity()));

// Any (R = s*G, s) "signature" now verifies under the group key:
// s*G == R + c * identity == R
```

Supporting code: `ThresholdKeys::read` share loop using `Ciphersuite::read_G` (crypto/dkg/src/lib.rs:620-623), group key derivation without identity check (crypto/dkg/src/lib.rs:376-378), canonical-but-identity-accepting `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101), and the identity-rejecting `Curve::read_G` that this path fails to use (crypto/frost/src/curve/mod.rs:123-131).