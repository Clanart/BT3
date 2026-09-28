### Title
`ThresholdKeys::read` accepts identity verification shares, permitting a zero/attacker-known group key - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to `addPool` accepting a zero `_gauge` (which downstream code then treats as a valid address and successfully calls), `ThresholdKeys::read`/`ThresholdKeys::new` accept identity group elements as verification shares without rejection. The group key is then computed by summing verification shares `1..=t`, so an all-identity set silently yields `group_key == identity` — a public key whose discrete logarithm (0) is known to everyone. `Curve::read_G` (FROST) explicitly rejects identity points, but `ThresholdKeys::read` deliberately uses `Ciphersuite::read_G`, which only enforces canonical encoding and permits the identity.

### Finding Description
`ThresholdKeys::read` reads `n` verification shares via `<C as Ciphersuite>::read_G` (dkg/src/lib.rs:620-623). `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101) checks only canonicity, not identity — unlike `Curve::read_G` (crypto/frost/src/curve/mod.rs:125-131), which returns `"identity point"` errors. `ThresholdKeys::new` (dkg/src/lib.rs:349-391) validates only the count and participant indexes (`<= n`); it never checks that verification shares are non-identity, and it never checks that `secret_share` is consistent with `verification_shares[i]`. The group key is derived purely from the attacker-controlled shares: `group_key = Σ_{i∈1..=t} verification_shares[i] * interpolation_factor(i)` (dkg/src/lib.rs:376-378).

Supplying identity-encoded points for participants `1..=t` makes `group_key` the identity element regardless of `secret_share`. Any subsequent signing (FROST `sign`, `ThresholdView`, etc.) produces signatures under a group key whose discrete log is 0 — trivially forgeable by anyone. Likewise, `secret_share` need not match its own verification share, so an attacker can also force an inconsistent view where this participant's share verifies against one key while the group key is another.

### Impact Explanation
An attacker who can feed crafted bytes to `ThresholdKeys::read` (listed as an untrusted-input sink) obtains a `ThresholdKeys` object whose `group_key()` is the identity (or any point with known discrete log, by setting a single non-identity share for `i ≤ t` and identity elsewhere). The victim signer then participates in "threshold" signing for a key the attacker fully controls — signatures the attacker could have produced alone — or participates in an inconsistent key where honest reconstruction is impossible. This is a forged-signature / incorrect-group-key impact, not merely a denial of service.

### Likelihood Explanation
Reachability depends on an integrator deserializing attacker-influenced `ThresholdKeys` blobs; the format is documented and `read` is a public API intended for key import (it is exercised exactly this way in `crypto/frost/src/tests/vectors.rs:118-131`). The DKG protocols (PedPoP, dealer, MuSig) construct `ThresholdKeys` internally with real shares, so the vulnerable path is external deserialization — which is precisely the untrusted-bytes surface in scope.

### Recommendation
In `ThresholdKeys::new` (or `ThresholdKeys::read`), reject identity verification shares and reject a resulting `group_key` that is identity, mirroring the identity rejection already present in `Curve::read_G`. Additionally, verify `C::generator() * secret_share == verification_shares[params.i()]` so the deserialized secret share is bound to its claimed verification share.

### Proof of Concept
```rust
use std::io;
use zeroize::Zeroizing;
use ciphersuite::{Ciphersuite, group::GroupEncoding};
use dkg::{ThresholdKeys, Interpolation};
use frost::curve::Ristretto;

// Build a serialized ThresholdKeys blob: t=1, n=1, i=1, Lagrange, any share,
// verification share = identity point.
let mut blob = vec![];
blob.extend((Ristretto::ID.len() as u32).to_le_bytes());
blob.extend(Ristretto::ID);
blob.extend(1u16.to_le_bytes()); // t
blob.extend(1u16.to_le_bytes()); // n
blob.extend(1u16.to_le_bytes()); // i
blob.push(1);                    // Interpolation::Lagrange
blob.extend(<Ristretto as Ciphersuite>::F::ONE.to_repr().as_ref()); // secret_share = 1
blob.extend(
  <Ristretto as Ciphersuite>::G::identity().to_bytes().as_ref(), // verification share = identity
);

// Accepted: Ciphersuite::read_G permits identity, ThresholdKeys::new does not reject it
let keys = ThresholdKeys::<Ristretto>::read::<&[u8]>(&mut blob.as_ref()).unwrap();
assert!(bool::from(keys.group_key().is_identity())); // group key has dlog 0 — forgeable
// secret_share=1 is inconsistent with the identity verification share, yet no error
```