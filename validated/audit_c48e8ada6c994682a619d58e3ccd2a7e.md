### Malicious FROST preprocess triggers a point-at-infinity panic during Bitcoin share verification - ([File: networks/bitcoin/src/crypto.rs])

### Summary

An unprivileged signing participant can send an all-identity FROST nonce commitment and an invalid signature share. The resulting bound nonce is exactly the point at infinity. When Bitcoin’s Schnorr verifier attempts to blame the participant, `x()` panics while extracting an x-coordinate from that point, terminating the signing process instead of returning `InvalidShare`.

### Finding Description

FROST preprocesses are parsed through `AlgorithmSignMachine::read_preprocess`, which delegates to `Commitments::read`. For the Bitcoin Schnorr algorithm, the planned nonce shape is `vec![vec![generator]]`, so a peer preprocess consists of two encoded `Secp256k1` points: `D` and `E`. No semantic check rejects `D = E = identity`.

Later, `BindingFactor::bound` computes the participant’s bound nonce as `D + rho * E`. With both commitments set to identity, the result is identity for every possible binding factor. If the same participant submits an invalid share, `AlgorithmSignatureMachine::complete` enters blame verification and calls `verify_share` using that bound nonce.

The generic Schnorr implementation constructs `SchnorrSignature { R: nonces[0][0], ... }`, where `nonces[0][0]` is the identity. Bitcoin’s `Hram::hram` passes this point into `x`, whose implementation unconditionally unwraps an x-coordinate. For the point at infinity there is no x-coordinate, so the signer panics.

Relevant flow:

- `AlgorithmSignMachine::read_preprocess` accepts commitments via `Commitments::read` without rejecting identity commitments: `crypto/frost/src/sign.rs:276-280`, `crypto/frost/src/nonce.rs:133-138`.
- `BindingFactor::bound` computes `D + rho * E`: `crypto/frost/src/nonce.rs:180-188`.
- `complete` invokes per-share verification with `B.bound(l)` after aggregate verification fails: `crypto/frost/src/sign.rs:462-481`.
- Bitcoin’s HRAM extracts `x(R)` unconditionally: `networks/bitcoin/src/crypto.rs:59-72`.
- `x` panics on infinity: `networks/bitcoin/src/crypto.rs:10-16`.

### Impact Explanation

This is a remotely reachable denial of signing for the Bitcoin threshold wallet. A malicious participant can repeatedly crash honest processors during blame attribution by pairing an identity preprocess with an invalid share. The signing attempt cannot complete and the process may terminate before cleanly reporting `InvalidShare`, preventing the transaction from being signed while the peer is included.

This analog matches the null-dereference bug class: attacker-controlled input reaches a missing-value path that assumes an optional/internal result is present.

### Likelihood Explanation

The attack only requires the adversary to participate in a Bitcoin FROST signing session and provide syntactically valid preprocess bytes. The preprocess does not need to predict the binding factor because `identity + rho * identity` remains identity. An arbitrary bad share then forces the blame path and the panic.

### Recommendation

Reject the identity point for FROST nonce commitments, or handle identity explicitly before invoking BIP-340 HRAM. At minimum, make `verify_share` return `Err(())` when `nonces[0][0].is_identity()` rather than allowing `x()` to panic. Add a regression test where a participant submits `D = E = identity` followed by an invalid scalar share and assert `FrostError::InvalidShare` is returned.

### Proof of Concept

```text
1. Initialize a normal Bitcoin TransactionSignMachine for a signing set
   containing attacker participant `a`.

2. For participant `a`, construct a Schnorr preprocess whose single nonce
   commitment is:
       D = Secp256k1 identity
       E = Secp256k1 identity

3. Send those bytes through `TransactionSignMachine::read_preprocess`, then
   call `sign`. The bound nonce for `a` becomes:
       D + rho * E = identity

4. In the share round, make `a` submit any scalar that does not represent a
   valid share.

5. `AlgorithmSignatureMachine::complete` fails aggregate verification and calls:
       algorithm.verify_share(
         view.verification_share(a),
         B.bound(a),       // vec![vec![identity]]
         invalid_share,
       )

6. Generic Schnorr builds:
       SchnorrSignature { R: identity, s: invalid_share }

7. Bitcoin `Hram::hram` calls `x(&identity)`, and `x` panics at:
       encoded.x().expect("point at infinity")
```