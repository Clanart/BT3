### Title
Schnorr verification accepts an identity public key and identity nonce, permitting universal forgery - ([File: crypto/schnorr/src/lib.rs](crypto/schnorr/src/lib.rs))

### Summary
Analogous to the Chainlink adapter consuming a `0` answer without validation, `SchnorrSignature::read` / `SchnorrSignature::verify` consume a nonce point `R` and public key `A` without checking either is non-identity (and without checking `s != 0`). Under an identity public key the verification equation degenerates and *any* scalar `r` yields a valid signature `(R = r·G, s = r)` regardless of the challenge — a universal forgery reachable purely through untrusted bytes fed to `SchnorrSignature::read` plus a caller-controlled/public `public_key` argument to `verify`.

### Finding Description
`SchnorrSignature::verify` checks `R + c·A − s·G == 0` via `batch_statements` and `multiexp_vartime`, with no identity checks on `self.R` or `public_key` and no non-zero check on `self.s` (`crypto/schnorr/src/lib.rs` lines 88–110). `SchnorrSignature::read` uses `C::read_G`/`C::read_F`, which enforce canonical encodings but accept the identity point and zero scalar (lines 51–53) — unlike `Curve::read_G` in `crypto/frost/src/curve/mod.rs` (lines 124–131), which explicitly rejects identity, the base `Ciphersuite::read_G` does not.

If `public_key` is the identity (`A = 0`, discrete log `a = 0`), the equation reduces to `R − s·G == 0`, satisfied by `s = r`, `R = r·G` for any `r`, for *any* challenge value. The challenge cannot bind to `A` in a way that helps because `c·A = 0` always. This is exactly the "returned/accepted value is zero and downstream code doesn't check it" class: the verifier treats the degenerate zero key as a valid basepoint.

The same unvalidated-identity issue propagates: `ThresholdKeys::read` (`crypto/dkg/src/lib.rs` lines 620–631) reads `verification_shares` with `Ciphersuite::read_G` (no identity rejection) and computes `group_key` as their interpolated sum (lines 376–378). Untrusted serialized `ThresholdKeys` encoding all-identity or summing-to-identity verification shares produce `group_key = identity`, after which the degenerate forgery applies to that "key" — and `complete` in `crypto/frost/src/sign.rs` (line 465) calls `algorithm.verify(view.group_key(), ...)` on it. Additionally `Interpolation::Constant` coefficients read at lines 607–612 are never checked non-zero, so a deserialized constant factor of `0` silently zeroes a participant's share weight.

### Impact Explanation
Any signature-verification path that accepts an attacker-influenced public key or deserialized `ThresholdKeys` can be forged: signatures "valid" under the identity group key require no secret material, so any consumer treating verification success as authorization (e.g., a signature produced by `SignatureMachine::complete` whose output is later re-verified elsewhere) accepts attacker-crafted signatures. The identity point is a valid canonical encoding, so it passes `SchnorrSignature::read`/`C::read_G` everywhere the stricter `Curve::read_G` isn't used.

### Likelihood Explanation
Requires a caller feeding an identity/degenerate public key or a maliciously crafted serialized `ThresholdKeys` blob into `verify`. The library's own `Curve::read_G` (FROST preprocess path) rejects identity, so the exposure is on paths using `SchnorrSignature::read` + `verify` or `ThresholdKeys::read` directly with untrusted bytes — which is precisely the exposed public API surface. Forgery itself is deterministic (choose `r`, set `s = r`, `R = r·G`), not probabilistic. Medium severity: the primitive permits an incorrect verifier result, but exploitation requires a consumer verifying under a degenerate key.

### Recommendation
In `SchnorrSignature::verify`/`batch_statements`, reject `R.is_identity()` and `public_key.is_identity()` (and optionally `s == 0`); in `SchnorrSignature::read`, use an identity-rejecting point read. In `ThresholdKeys::read`/`ThresholdKeys::new`, reject identity verification shares and assert `group_key` is non-identity. In `Interpolation::Constant` deserialization, reject zero coefficients.

### Proof of Concept
```rust
use zeroize::Zeroizing;
use rand_core::OsRng;
use group::{Group, ff::Field};
use ciphersuite::Ciphersuite;
use schnorr::SchnorrSignature;

// For any Ciphersuite C:
let r = Zeroizing::new(C::random_nonzero_F(&mut OsRng));
let forgery = SchnorrSignature::<C> { R: C::generator() * *r, s: *r };

// identity public key, arbitrary challenge
let A = C::G::identity();
let c = C::random_nonzero_F(&mut OsRng);

// verify computes R + c*A - s*G = r*G + 0 - r*G = 0  -> passes
assert!(forgery.verify(A, c));
```
The signature `(R = r·G, s = r)` verifies for *every* challenge under `A = identity`, and `A = identity` is a canonically encoded point accepted by `SchnorrSignature::read`/`C::read_G`.