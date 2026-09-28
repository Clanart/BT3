### Title
Missing identity check on verification shares allows a zero group key anyone can sign for - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::new` validates the count and index bounds of `verification_shares`, but never rejects the identity (zero) group element for either the individual shares or the resulting `group_key`. Because `ThresholdKeys::read` deserializes those shares via `<C as Ciphersuite>::read_G` — which enforces canonical encoding only, not non-identity — a caller can end up with `ThresholdKeys` whose `group_key` is the identity point. The identity public key has discrete log 0, so Schnorr signatures over it are trivially forgeable by anyone.

### Finding Description
The external report's bug class is "a critical address/point parameter is accepted without a zero check, and once set, funds relying on it are lost." In Serai the analogous parameter is the threshold group's public key, derived inside `ThresholdKeys::new` from attacker-influenceable `verification_shares`:

- `ThresholdKeys::new` checks only `verification_shares.len() == n` and that each `Participant` index `<= n` (`crypto/dkg/src/lib.rs:355-365`). There is no `is_identity()` rejection on any share.
- The group key is then computed as `sum(verification_shares[i] * interpolation_factor(i))` over participants `1..=t` (`crypto/dkg/src/lib.rs:376-378`) with no check that the result is non-identity.
- `ThresholdKeys::read` reconstructs shares with `<C as Ciphersuite>::read_G` (`crypto/dkg/src/lib.rs:620-623`), and `Ciphersuite::read_G` only enforces canonical encoding (`crypto/ciphersuite/src/lib.rs:91-101`). The stricter identity-rejecting `Curve::read_G` in `crypto/frost/src/curve/mod.rs:125-131` is *not* used on this path.
- `group_key()` returns `group_key * scalar + G * offset` (`crypto/dkg/src/lib.rs:445-447`); with an identity `group_key` and default scalar/offset, the published group key is the identity.

Verification then degenerates: `SchnorrSignature::verify` checks `R + cA - sG == 0` (`crypto/schnorr/src/lib.rs:88-110`), and with `A = identity` any `(R, s)` with `s*G == R` verifies — e.g., pick any `s`, set `R = s*G`. No secret knowledge is required.

### Impact Explanation
A group key equal to the identity point is a public key with known discrete log. Any party can produce valid FROST/Schnorr signatures for it without any threshold participation, and any funds/outputs locked to that key are spendable by an attacker rather than the group — the exact "loss of funds via zero-value parameter" impact of the source finding. Additionally, even a partial version (one identity verification share) corrupts the share-verification assumption that `verification_shares[l]` is a non-trivial commitment to participant `l`'s share.

### Likelihood Explanation
`ThresholdKeys::read` is in the enumerated untrusted-bytes surface, and `ThresholdKeys::new` is the API DKG finalization paths feed verified shares into. If all verification shares are the identity (or a linear combination evaluating to identity under the interpolation factors — e.g., Constant interpolation with crafted coefficients), the resulting `ThresholdKeys` is accepted as valid and serializable. Honest DKG never produces this, but the type system and serialization format do not prevent it, and nothing downstream re-checks `group_key` before it is used as a signing/verifying authority.

### Recommendation
In `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349`), reject the identity for each element of `verification_shares` and for the computed `group_key` before constructing `ThresholdCore`, returning a `DkgError`. This makes the invariant ("the group key has an unknown discrete log") enforced at construction and covers every deserialization path, including `ThresholdKeys::read`.

### Proof of Concept
```rust
// crypto/dkg — concept, using dalek_ff_group::Ed25519 as C
use std::collections::HashMap;
use zeroize::Zeroizing;
use ciphersuite::{group::Group, Ciphersuite};
use dkg::{ThresholdParams, ThresholdKeys, Interpolation, Participant};

// t = n = 1, i = 1: Constant interpolation with coefficient 1
let params = ThresholdParams::new(1, 1, Participant::new(1).unwrap()).unwrap();
let mut shares = HashMap::new();
// Attacker-supplied / corrupted bytes: identity verification share.
// <Ed25519 as Ciphersuite>::read_G accepts the canonical identity encoding.
shares.insert(Participant::new(1).unwrap(), <Ed25519 as Ciphersuite>::G::identity());

let keys = ThresholdKeys::<Ed25519>::new(
    params,
    Interpolation::Constant(vec![<Ed25519 as Ciphersuite>::F::ONE]),
    Zeroizing::new(<Ed25519 as Ciphersuite>::F::ONE), // arbitrary secret_share
    shares,
).unwrap(); // accepted — no identity check

// group_key == identity == 0*G: discrete log is publicly known (0)
assert!(bool::from(keys.group_key().is_identity()));

// Anyone now forges a Schnorr signature for this "group key":
// pick any s, set R = s*G; then R + c*identity - s*G == 0 verifies.
```

Caveat: whether `ThresholdKeys` bytes cross a genuine trust boundary in deployment depends on integrator usage; within the crate itself the missing check is confirmed by direct inspection of `ThresholdKeys::new` and `ThresholdKeys::read`.