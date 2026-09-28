### Title
PedPoP `Commitments::read` accepts identity commitments, enabling a proof-of-knowledge forgery without knowing the discrete log - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The XML-external-entity report is, at its core, a *parser accepting untrusted input that encodes a semantically dangerous value without validation*. Serai's analog surface is `read_G`/`read_F` deserialization of attacker-controlled bytes. `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101) enforces canonicality but deliberately does **not** reject the identity point; the FROST-specific `Curve::read_G` (crypto/frost/src/curve/mod.rs:125-131) adds an identity check precisely because identity is dangerous in proofs. However, PedPoP's `Commitments::read` (crypto/dkg/pedpop/src/lib.rs:109-128) calls the base `C::read_G`, so any of the `t` polynomial commitments — including `commitments[0]`, the target of the Schnorr proof of knowledge — may be the identity element.

### Finding Description
In `verify_r1` (crypto/dkg/pedpop/src/lib.rs:300-338), the proof-of-knowledge signature is batch-verified with `msg.commitments[0]` as the public key and `challenge::<C>(context, l, R_bytes, cached_msg)` as the challenge (lines 323-329). The Schnorr verification statement is `s·G == R + c·A`. When `A = commitments[0]` is the identity, this degenerates to `s·G == R`, which any party can satisfy trivially by choosing an arbitrary scalar `r`, setting `R = r·G` and `s = r` — no knowledge of a discrete logarithm is involved, and the rest of the commitment vector (`commitments[1..t]`) is unconstrained.

An unprivileged participant can therefore submit a `Commitments` message in which the coefficient-0 commitment is identity, and `batch_verify` (via `BatchVerifier::verify_vartime_with_vartime_blame`, line 334) will accept the accompanying "proof of knowledge" as valid. The PoK exists specifically to force each PedPoP participant to prove knowledge of the discrete log of their commitment vector's constant term before the shares phase proceeds; this check is completely bypassed for identity submissions.

Contrast with FROST, where the codebase recognized the same hazard and rejects identity at the `Curve::read_G` layer (crypto/frost/src/curve/mod.rs:127-129). No equivalent rejection exists anywhere in the PedPoP commitment path: `Commitments::read` uses `C::read_G` (line 118), and neither `verify_r1` nor `generate_secret_shares` performs an identity check on `msg.commitments[0]` or on the remaining coefficients before queueing share-verification statements in `calculate_share` (lines 487-491).

### Impact Explanation
Concrete impact: a forged proof. An attacker can broadcast commitments satisfying `verify_r1`'s batch verification without possessing the secret the PoK is meant to bind them to, breaking the PedPoP security precondition that every participant is committed to a polynomial they know the constant term of. Downstream, share-verification statements built by `share_verification_statements`/`exponential` (lines 420-449) treat these identity points as legitimate commitments; a participant whose entire commitment vector is identity can send a zero secret share that verifies, and the resulting `verification_shares`/`group_key` in `ThresholdKeys::new` (lines 511-530) silently absorb the identity contribution. The protocol completes "successfully" while the knowledge-soundness guarantee the FROST paper relies on (to prevent key-bleeding / adaptive-cancellation attacks) is violated for that participant. Since the forgery bypasses the only mechanism preventing commitments from being chosen without knowledge of their discrete logs, it can be combined with seeing honest parties' commitments first — the attacker's identity `A0` needs no discrete log, so it imposes no constraint when adaptively selecting the remaining commitment entries relative to observed honest commitments.

### Likelihood Explanation
Reachable by any unprivileged DKG participant: `Commitments` bytes are broadcast inputs read via `Commitments::read` and fed directly into `verify_r1` → `SchnorrSignature::batch_verify`. No collusion, leaked key, or malicious infrastructure is required; the attacker simply serializes the identity encoding in the commitment vector and supplies a trivially-constructed `(R, s)` pair. Deterministic and cost-free to exploit.

### Recommendation
In `KeyGenMachine`/`SecretShareMachine::verify_r1` (crypto/dkg/pedpop/src/lib.rs), reject identity points in `msg.commitments` — at minimum `commitments[0]` — before queuing the PoK into the `BatchVerifier`. Alternatively, have `Commitments::read` enforce non-identity via an explicit `is_identity` check (as `Signed::read` in coordinator/tributary/src/transaction.rs:63-69 already does for signature nonces). All `t` commitments should be checked, since identity coefficients deeper in the vector also weaken the binding of `share_verification_statements`.

### Proof of Concept
```rust
// Inside a PedPoP round-1 message construction (attacker-controlled bytes):
let t = params.t();
let mut msg_bytes = vec![];

// Commitment vector: coefficient-0 commitment is the IDENTITY point.
// Ciphersuite::read_G accepts it — canonical and non-identity are not checked.
msg_bytes.extend(C::G::identity().to_bytes().as_ref());
for _ in 1 .. t {
    msg_bytes.extend(C::G::identity().to_bytes().as_ref()); // or arbitrary points
}

// PoK "signature" for A0 = identity: statement is s*G == R + c*A == R.
// Pick r = 1: R = G, s = 1 satisfies verification regardless of challenge c.
let r = C::F::ONE;
let R = C::generator() * r;
let forged = SchnorrSignature::<C> { R, s: r };
forged.write(&mut msg_bytes).unwrap();

// Commitments::read succeeds (C::read_G, lib.rs:118 does not reject identity),
// and verify_r1's batch_verify accepts: s*G == R == R + c*identity.
// The participant proceeds to round 2 with a forged proof of knowledge.
```
The bytes above pass `Commitments::<C>::read` and cause `batch.verify_vartime_with_vartime_blame()` at crypto/dkg/pedpop/src/lib.rs:334 to return `Ok(())`, despite the sender knowing no discrete logarithm for `commitments[0]`.