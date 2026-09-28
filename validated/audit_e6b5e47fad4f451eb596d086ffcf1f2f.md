### Title
`ThresholdKeys::read` accepts identity verification shares, yielding a group key of identity for which signatures are forgeable without any secret - (crypto/dkg/src/lib.rs)

### Summary
The Illuminate bug class: a security-critical input is never validated for the degenerate/zero case, so a guard that is supposed to enforce "value was actually deposited" silently succeeds, and the protocol proceeds to hand out output. In Serai, `ThresholdKeys::<C>::read` deserializes the per-participant verification shares with `<C as Ciphersuite>::read_G` (crypto/dkg/src/lib.rs:620-623), which performs only canonical-encoding checks. It does not use `Curve::read_G` (crypto/frost/src/curve/mod.rs:124-131), the variant that explicitly rejects the identity point. An attacker supplying crafted bytes therefore obtains a `ThresholdKeys` whose verification shares are all identity. `ThresholdKeys::new` computes `group_key` as the interpolation-weighted sum of `verification_shares[1..=t]` (crypto/dkg/src/lib.rs:376-378) without checking the result is non-identity, so the resulting group key is the identity point. During FROST completion, each participant's share is checked against `s·G == bound_nonce + c·V_l` via `SchnorrSignature::batch_statements`/`verify_share` (crypto/frost/src/algorithm.rs:219-230, crypto/frost/src/sign.rs:475-489). With `V_l = identity`, the challenge term vanishes, so anyone who chose their own preprocess commitments `D = r·G`, `E = e·G` can emit a "valid" share `s = r + ρe` with no private key at all. The summed signature `(R = Σ bound nonces, s = Σ shares)` satisfies `R + c·group_key - s·G = 0` for `group_key = identity` on any message — a forgery.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reads `t`, `n`, `i`, the interpolation variant, a `secret_share`, and then `n` verification shares via `<C as Ciphersuite>::read_G` at line 622. Unlike `Curve::read_G`, `Ciphersuite::read_G` accepts the canonical encoding of the identity point. `ThresholdKeys::new` then computes `group_key = Σ_{i=1..=t} verification_shares[i] * interpolation_factor(i, [1..=t])` and stores it unconditionally — there is no check that `group_key` (or any share) is non-identity. This is exactly parallel to `Safe.transferFrom(IERC20(address(0)), ...)` silently succeeding: the zero/identity input is never rejected, so the downstream "mint" (signature production/verification) proceeds on a degenerate credential.

Because each verification share is identity, `ThresholdView::view` (crypto/dkg/src/lib.rs:500-507) produces identity interpolated verification shares for every included participant. In `AlgorithmSignatureMachine::complete` (crypto/frost/src/sign.rs:447-495), the aggregate `verify` reduces to `R - s·G == 0`, which the attacker trivially satisfies: each controlled participant preprocesses commitments `D = r·G, E = e·G` (non-identity, so `Commitments::read` at crypto/frost/src/nonce.rs:34-36 accepts them), and submits share `s_i = r_i + ρ_i·e_i` — no secret share is ever required, since the `c·λ_i·V_i` term is `c·λ_i·identity = 0`.

### Impact Explanation
An unprivileged attacker who can feed crafted bytes to `ThresholdKeys::read` (a listed untrusted-bytes entry point) obtains keys whose `group_key` is the identity point, and can then drive `AlgorithmMachine`/`AlgorithmSignMachine`/`AlgorithmSignatureMachine::complete` to emit a `SchnorrSignature` that passes `verify` for arbitrary messages — a forged threshold signature produced with zero secret material. Any consumer trusting "valid signature under `group_key`" (e.g., a deserialized validator set authorizing batches, cosigns, or slash reports) is fully bypassed, analogous to minting arbitrary principal tokens without depositing anything.

### Likelihood Explanation
The flaw is deterministic and requires no cryptanalytic work: all values are attacker-chosen bytes (`t`, `n`, interpolation, `secret_share`, and `n` identity encodings, e.g., the all-zero or canonical identity encodings accepted by `Ciphersuite::read_G` for Ristretto/k256). Likelihood of exploitation depends on whether serialized `ThresholdKeys` ever cross a trust boundary; where they do, exploitation is certain. Note `Curve::read_G` exists precisely to reject identity, making the omission in `ThresholdKeys::read` inconsistent with the codebase's own hygiene — preprocess commitments and FROST points already use the rejecting variant.

### Recommendation
In `ThresholdKeys::read` (crypto/dkg/src/lib.rs:620-623), reject identity verification shares (e.g., use `Curve::read_G`-equivalent checks or an explicit `is_identity` check per share), and in `ThresholdKeys::new` additionally reject an identity `group_key` after interpolation. Mirror the precedent already set by `Signed::read` (coordinator/tributary/src/transaction.rs:62-69), which rejects identity signature nonces.

### Proof of Concept
```rust
// Attacker crafts bytes for ThresholdKeys::<C>::read:
//   C::ID len || C::ID || t = 2 || n = 2 || i = 1 ||
//   interpolation = 1 (Lagrange) ||
//   secret_share = 0 ||
//   verification_shares = [identity_encoding, identity_encoding]
//
// Ciphersuite::read_G accepts the canonical identity encoding, so
// `ThresholdKeys::read` succeeds where `Curve::read_G` would have errored.
let keys = ThresholdKeys::<C>::read(&mut crafted.as_slice()).unwrap();
assert!(bool::from(keys.group_key().is_identity())); // group key is identity

// Attacker-controlled participants 1 and 2 then run FROST with commitments
// D = r·G, E = e·G (non-identity, accepted by Commitments::read) and submit
// shares s_i = r_i + rho_i * e_i. Each share passes verify_share because
// c * lambda_i * V_i = c * lambda_i * identity = 0.
//
// complete() returns SchnorrSignature { R: sum(bound nonces), s: sum(shares) }
// with s * G == R, so sig.verify(group_key = identity, c) holds for ANY msg —
// a signature produced with no secret key share at all.
```

All mechanics are grounded in: `Ciphersuite::read_G` vs `Curve::read_G` identity rejection (crypto/frost/src/curve/mod.rs:124-131), verification-share deserialization (crypto/dkg/src/lib.rs:620-623), group key derivation (crypto/dkg/src/lib.rs:376-378), the `sG == R + cA` statement shape (crypto/schnorr/src/lib.rs:88-100), and the share-verification/blame path (crypto/frost/src/sign.rs:465-489).