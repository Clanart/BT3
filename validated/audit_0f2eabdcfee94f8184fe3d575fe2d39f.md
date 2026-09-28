### Title
Missing consistency check between `secret_share` and `verification_shares` lets semantically invalid `ThresholdKeys` persist and permanently break signing - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to `LibEntity._updateEntity()` validating the stored `utilizedCapacity` while letting a derived value exceed `maxCapacity`, `ThresholdKeys::new` validates the *shape* of the inputs (count/range of `verification_shares`, `t <= n`, `i <= n`, applicable `Interpolation`) but never validates the semantic invariant that the local `secret_share` actually corresponds to this participant's verification share (`C::generator() * secret_share == verification_shares[i]`). `ThresholdKeys::read` feeds fully attacker-controlled bytes into this constructor, so malformed bytes yield a structurally valid, semantically inconsistent key set. That invalid state then persists silently and only surfaces at signing time, where every attempt fails.

### Finding Description
`ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) performs these checks only:
- `verification_shares.len() == n` and every key `<= n` (lines 355-365)
- `Interpolation::Constant` requires `t == n` (lines 367-374)
- `ThresholdParams::new` enforces `t <= n`, `i <= n`, non-zero (lines 166-179)

It then computes `group_key` purely from `verification_shares` (interpolation of shares 1..=t, lines 376-378) and stores the untrusted `secret_share` without checking it against `verification_shares[params.i()]`.

`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reads `t`, `n`, `i`, the interpolation method, `secret_share`, and `n` verification shares from an arbitrary `io::Read`, then calls `ThresholdKeys::new`. Nothing in the deserialization path rejects a `secret_share` inconsistent with the verification shares.

At `view()` time (crypto/dkg/src/lib.rs:463-533) the interpolated `secret_share` is paired with per-participant interpolated `verification_shares` that are now inconsistent with it. In `AlgorithmSignatureMachine::complete` (crypto/frost/src/sign.rs:447-495), the aggregated `sum` fails `algorithm.verify`, and blame assignment fails too because the honest secret share was wrong: the code itself comments (lines 491-494) that the only known way to reach this state is "to deserialize a semantically invalid FrostKeys", returning `FrostError::InternalError`. So the invalid key material permanently denies the holder the ability to produce valid signatures — the exact "invalid state persists and denies the ability to do work" shape of the reported bug.

### Impact Explanation
An unprivileged party who can get crafted bytes into `ThresholdKeys::read` (e.g., a corrupted/hostile key blob delivered to a signer) causes that signer to hold a permanently inconsistent `ThresholdKeys`: it can never produce a valid signature share and cannot even correctly blame peers — every `complete` ends in `InternalError` after a wasted blame `BatchVerifier` pass. The group effectively loses this participant's signing capacity for that key, which in a t-of-n deployment can stall threshold signing indefinitely (funds locked behind the group key cannot move while enough signers are poisoned). This mirrors the report's impact of an Entity being unable to write new policies.

### Likelihood Explanation
Medium. Exploitation requires feeding crafted bytes to `ThresholdKeys::read`, i.e., control over a serialized keys blob rather than just protocol messages, so reachability is weaker than pure wire inputs — but it is explicitly an untrusted-bytes entry point listed as in scope, and unlike the original bug no cryptographic primitive or honest-participant misbehavior is needed; the missing check is unconditional.

### Recommendation
In `ThresholdKeys::new`, after the existing structural checks, verify `verification_shares[&params.i()] == C::generator() * secret_share.deref()` and reject with a new `DkgError` variant (e.g., `InconsistentSecretShare`) if it does not hold. This makes `ThresholdKeys::read` — and any other producer such as `GeneratorPromotion::complete` — incapable of constructing a semantically invalid key set. Optionally, also verify the `Interpolation::Constant` vector length equals `n` so `interpolation_factor` (which indexes `c[i-1]`) can never be driven out of bounds.

### Proof of Concept
```rust
// Attacker-crafted bytes -> ThresholdKeys::<C>::read succeeds with inconsistent state
// Layout per ThresholdKeys::read (crypto/dkg/src/lib.rs:574-632):
//   [4B ID len][C::ID][t:u16le][n:u16le][i:u16le][interp tag][(n F elems if Constant)]
//   [secret_share F][n G elems]
//
// Construct with t=2, n=3, i=1, Interpolation::Lagrange (tag 1),
// secret_share = arbitrary scalar s (NOT the share matching verification_shares[1]),
// verification_shares = any 3 non-identity points P1, P2, P3.
//
// ThresholdKeys::new returns Ok(..) because:
//   - verification_shares.len() == 3 == n
//   - all participant keys 1..=3 <= n
//   - no check that C::generator() * s == P1
//
// Later, AlgorithmSignMachine::sign -> view() computes
//   secret_share' = lagrange(1, included) * s
// but verification_shares[1] interpolates to lagrange(1, included) * P1,
// so this signer emits a share that fails the aggregate Schnorr verify.
// AlgorithmSignatureMachine::complete then queues verify_share for every
// participant (all individually consistent), finds no blame, and hits:
//   Err(FrostError::InternalError(
//     "everyone had a valid share yet the signature was still invalid"))
// — unreachable by protocol peers, only by the semantically invalid keys.
```