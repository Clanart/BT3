### Title
Identity group elements accepted by `read_G` allow a universally valid Schnorr forgery (`R = identity`, `s = 0`) and unvetted zero-value nonce commitments — ([File: crypto/schnorr/src/lib.rs](crypto/schnorr/src/lib.rs))

### Summary
The external report's bug class is: a value returned from an external/untrusted source is consumed without a zero/invalidity check, letting a degenerate `0` propagate into downstream math (`periodIndex == 0`) that the protocol explicitly treats as a broken state. In Serai, the analogous unchecked "zero" is the group identity element. `Ciphersuite::read_G` enforces canonicality but explicitly does **not** reject the identity point, and `SchnorrSignature::verify` performs no identity check on either `R` or `public_key`. This makes the signature `(R = identity, s = 0)` a valid signature for **any** challenge/message under the identity public key, and lets identity nonce commitments flow into FROST's binding math unchecked.

### Finding Description
`read_G` in `crypto/ciphersuite/src/lib.rs` only checks that the encoding round-trips; the identity element is a canonical encoding and is accepted.

`SchnorrSignature::verify` checks `R + c·A − s·G == 0` via `multiexp_vartime`. If `A = identity` and the signature is `(R = identity, s = 0)`, the statement is `identity + 0 − 0 = identity` — it verifies for **every** challenge `c`, i.e., a universal forgery over all messages for that key.

Every entry point listed as attacker-reachable feeds this path without an identity check:

- `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:620-631`) reads all `n` verification shares with `read_G` and never rejects identity; `ThresholdKeys::new` then computes `group_key` as a weighted sum of those shares, so crafted bytes yield a degenerate/`identity` group key or arbitrary attacker-controlled group key.
- `Commitments::read` (`crypto/frost/src/nonce.rs:33-36, 133-139`) reads `D`/`E` preprocess points via `read_G` with no identity rejection, so a peer can submit `(D, E) = (identity, identity)`, contributing a bound nonce of `identity + ρ·identity = identity` regardless of the binding factor.
- `musig::check_keys` (`crypto/dkg/musig/src/lib.rs:46-63`) rejects duplicate keys but not the identity key.

### Impact Explanation
Two concrete impacts:

1. **Signature forgery under a zero/identity key.** Wherever a public key originates from untrusted bytes (deserialized `ThresholdKeys` verification shares, protocol-supplied key lists feeding `musig_key`/`SchnorrSignature::verify`), the identity point is accepted as a "key". `(identity, 0)` then verifies for every challenge — a forged signature for any message against that key, exactly mirroring the report's "zero value silently accepted into security-critical math".

2. **Degenerate FROST nonces.** A signer submitting `D = E = identity` commits to a nonce contribution that is provably identity and independent of `ρ`. While their signature share is still individually verified in the blame path, the final `R` silently absorbs a zero contribution, and the batch-verifier/blame machinery operates on identity statements that were never intended to be representable.

### Likelihood Explanation
Reaching impact 1 requires a context where the verifying key is attacker-influenced (crafted `ThresholdKeys` serialization, or an integrator accepting an identity key). `ThresholdKeys::read` is explicitly a reachable untrusted-bytes entry point and performs no semantic validation of the shares beyond count/index bounds, so the identity shares and resulting degenerate `group_key` are directly attainable. Impact 2 is reachable by any signing peer via `read_preprocess`. The primitive gap (no identity rejection anywhere in `read_G` consumers) is unconditional.

### Recommendation
- Reject identity in `read_G` consumers that treat the point as a key or commitment (add a `point.is_identity()` check in `ThresholdKeys::read`/`ThresholdKeys::new` for verification shares, in `Commitments::read`/`GeneratorCommitments::read` for `D`/`E`, and in `check_keys` for MuSig).
- In `SchnorrSignature::verify`/`batch_statements` callers, reject `public_key == identity` and `R == identity` (FROST-consistent behavior; an `s = 0` share alone is already caught by share verification).
- Document the invariant explicitly, as the original fix did with `newTimestamp > 0`.

### Proof of Concept
```rust
// For a public key of identity, (R, s) = (identity, 0) verifies for ANY challenge.
let a: <C as Ciphersuite>::G = <C as Ciphersuite>::G::identity();
let sig = SchnorrSignature::<C> {
    R: <C as Ciphersuite>::G::identity(),
    s: <C as Ciphersuite>::F::ZERO,
};
for _ in 0 .. 256 {
    let c = <C as Ciphersuite>::F::random(&mut OsRng); // arbitrary challenge/message
    assert!(sig.verify(a, c)); // universally valid — forgery for every message
}

// read_G accepts the canonical identity encoding; ThresholdKeys::read then
// accepts identity verification shares and computes a degenerate group_key.
```

Caveat noted for completeness: I verified the missing identity checks in `read_G`, `SchnorrSignature::verify`, `Commitments::read`, `ThresholdKeys::read`, and `check_keys` directly. Whether a production caller plumbs an attacker-supplied key into `verify` without an upstream identity check depends on integrator usage outside the indexed scope; within-scope, the forged-signature impact is demonstrated for the identity key itself and the degenerate-input acceptance in `ThresholdKeys::read`/`Commitments::read` is unconditional.