### Title
Identity-point verification shares accepted by `ThresholdKeys::read` — the identity rejection hardening in `Curve::read_G` was never applied to key deserialization - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
Serai hardened its FROST message path against identity points: `Curve::read_G` explicitly rejects the identity after calling `Ciphersuite::read_G` (`crypto/frost/src/curve/mod.rs:125-130`). That hardening was never propagated to the parallel deserialization path for long-lived key material. `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:620-623`) reads all `n` verification shares via `<C as Ciphersuite>::read_G`, which performs canonical-encoding checks but does **not** reject the identity point (`crypto/ciphersuite/src/lib.rs:91-101`). The result is that attacker-supplied bytes can construct a `ThresholdKeys` whose `group_key` — computed in `ThresholdKeys::new` as the interpolated sum of verification shares `1..=t` (`crypto/dkg/src/lib.rs:376-378`) — is the identity, or where individual participants have identity verification shares. This mirrors the advisory's shape: a security check exists on one route (preprocess/commitment reading) while a second, publicly reachable route (`ThresholdKeys::read`, explicitly in scope) silently lacks it.

### Finding Description
Two complementary point readers exist:

- `Ciphersuite::read_G` — canonical encoding only, accepts identity.
- `Curve::read_G` — wraps the above and adds `if res.is_identity() { Err("identity point") }`, the hardening used for all FROST wire data (`GeneratorCommitments::read`, `Preprocess`, etc.).

`ThresholdKeys::read` uses the unhardened reader for `verification_shares`. `ThresholdKeys::new` then validates only the *count* of shares and participant indexes (`crypto/dkg/src/lib.rs:355-365`) — never that any share is non-identity — and derives `group_key` from them. `scale()` rejects a zero scalar (`crypto/dkg/src/lib.rs:400-403`) but nothing rejects an identity group key or identity verification share, so a fully "valid" `ThresholdKeys` is returned. `ThresholdView::verification_share` and `Algorithm::verify` / `verify_share` (`crypto/frost/src/algorithm.rs:214-230`) then operate on these poisoned shares: the Schnorr batch statement `R + cA - sG == 0` (`crypto/schnorr/src/lib.rs:88-99`) is trivially satisfiable when `A` (the verification share or the group key) is the identity, by choosing `s = r`, `R = rG`, and any `c`.

### Impact Explanation
Any component that obtains group keys or verifies shares via deserialized `ThresholdKeys` accepts forged input:

- **Forged aggregate signature**: if the bytes encode verification shares `1..=t` all equal to identity, `group_key` is identity and `SchnorrSignature { R: rG, s: r }` verifies under `c` for any message — a forgery against the group's public key with no secret knowledge.
- **Forged signature share / blame bypass**: an identity verification share for participant `l` makes `verify_share` accept `s = r` with `R = rG`, so a share "from" `l` validates without `l`'s key share — defeating share-level blame attribution in `AlgorithmSignatureMachine::complete` (`crypto/frost/src/sign.rs:474-489`) and enabling production of a signature attributing contributions to a non-participating party.

Because `ThresholdKeys::read` is a listed attacker-reachable entry point (serialized keys cross trust boundaries in recovery/transport), this yields concrete signature/share forgery, not merely a panic or DoS.

### Likelihood Explanation
Exploitation requires an attacker to feed crafted bytes to `ThresholdKeys::read` and have the resulting keys used for verification or signing — i.e., any flow that round-trips key material through storage, backups, or peer-provided recovery data. The primitive itself is fully deterministic (encoding identity points is trivial), so no grinding is needed once such a flow exists. That precondition is real but narrower than a pure wire attack, which is why this is not maximal severity.

### Recommendation
In `ThresholdKeys::read` (and equivalently `ThresholdKeys::new`), reject identity verification shares — read with `Curve::read_G` semantics or add an explicit `is_identity` check per share — and reject a computed identity `group_key`. Apply the same check anywhere `Ciphersuite::read_G` is used for points that later serve as verification keys (e.g., PedPoP `Commitments::read` at `crypto/dkg/pedpop/src/lib.rs:115-125` reads commitments without identity rejection, though its PoK partially constrains `commitments[0]`).

### Proof of Concept
```rust
// Conceptual PoC (Secp256k1 ciphersuite):
// 1. Build a ThresholdKeys byte string for t = n = 1, i = 1, Lagrange:
//    ID len || ID || t=1 || n=1 || i=1 || interp=1 || secret_share=0
//    || verification_shares[1] = identity (32/33-byte identity encoding).
// 2. ThresholdKeys::<Secp256k1>::read(&mut bytes) -> Ok(keys)
//    keys.group_key() == ProjectivePoint::IDENTITY  // no rejection anywhere
// 3. For any message msg:
//    c = hram(R, identity_group_key, msg); pick r, R = rG, s = r
//    SchnorrSignature { R, s }.verify(identity_group_key, c)
//    => R + c*IDENTITY - sG == R - R == identity -> true
// Forged Schnorr signature under the group's key without any private key share.
```
Verified against: `Ciphersuite::read_G` lacking an identity check (`crypto/ciphersuite/src/lib.rs:91-101`), `Curve::read_G` containing the missing hardening (`crypto/frost/src/curve/mod.rs:125-130`), `ThresholdKeys::read` using the unhardened reader (`crypto/dkg/src/lib.rs:620-623`), `ThresholdKeys::new` computing `group_key` from shares without identity checks (`crypto/dkg/src/lib.rs:355-390`), and the satisfiable verification formula (`crypto/schnorr/src/lib.rs:88-109`).

Caveat: I could not fully trace every consumer of `ThresholdKeys::read` within the iteration budget; the impact rating assumes a flow where deserialized keys drive verification/signing, which the in-scope rules explicitly designate as attacker-reachable.