The unchecked-zero-address bug class maps to unchecked identity/zero values read from untrusted bytes. `ThresholdKeys::read` is explicitly in the reachable set. Checking whether it uses the identity-rejecting `Curve::read_G` or the permissive `Ciphersuite::read_G`, and how the resulting group key is used.### Title
ThresholdKeys deserialization accepts identity verification shares and a zero secret share, yielding a forgeable group key - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::<C>::read` reconstructs a threshold key from untrusted bytes by calling `<C as Ciphersuite>::read_G` for each verification share and `C::read_F` for the secret share. The `Ciphersuite::read_G` implementation only enforces a canonical encoding; it does **not** reject the identity (zero) point, and `read_F` does not reject a zero scalar. This is the Serai analog of the Lido "unchecked address"/missing zero-check report: a value read from external input is used without validating it is non-zero/non-identity. Notably, `frost::Curve::read_G` *does* reject identity (`crypto/frost/src/curve/mod.rs:125-131`), but `ThresholdKeys::read` deliberately invokes the permissive `Ciphersuite::read_G`, bypassing that check.

### Finding Description
In `crypto/dkg/src/lib.rs`:

- `ThresholdKeys::read` reads `n` verification shares with `<C as Ciphersuite>::read_G(reader)` (line 622) and the secret share with `C::read_F(reader)` (line 618).
- `Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`) checks only canonicality — `from_bytes` in `dalek-ff-group` accepts the identity encoding, so the identity point round-trips cleanly.
- `ThresholdKeys::new` (lines 349-391) validates share counts, participant indexes, and interpolation applicability, but never checks that verification shares or the group key are non-identity. It computes `group_key` as `sum(verification_shares[i] * interpolation_factor(i))` over `1..=t` (lines 376-378). With all shares set to identity, `group_key` is the identity point.
- `ThresholdView`/`view` (lines 463-533) interpolates these shares without any identity check either; each verification share is `identity * scalar * factor = identity`.

Downstream, `SchnorrSignature::verify` (`crypto/schnorr/src/lib.rs:88-110`) checks `R + c·A − s·G == 0`. With `A = identity`, `c·A = 0`, so any `(R, s)` with `R = s·G` verifies — signatures for the deserialized group key are trivially forgeable by anyone, for arbitrary messages. In FROST, `Schnorr::verify` (`crypto/frost/src/algorithm.rs:214-217`) builds the signature over `group_key` and the nonce sum and calls the same `verify`, so a "signature" the attacker fabricates passes. `verify_share` batch statements likewise degenerate: a share-verification against an identity verification share checks `s_i·G == R_i`, which the attacker satisfies freely.

### Impact Explanation
An attacker who can supply serialized `ThresholdKeys` bytes (explicitly in scope as untrusted input to `ThresholdKeys::read`) can install a key set whose group key is the identity — a key whose discrete log is publicly known (zero). Any funds, validator-set authority, or signed state attributed to that group key are forgeable/stealable, and every signature accepted under it is a forged signature. A related zero-scalar variant (zero `secret_share` with consistent identity verification share) deserializes to a fully known key share. In key-recovery flows (`crypto/dkg/recovery`, `promote`), reconstructed keys originate from peer-supplied material, making attacker-controlled bytes a realistic input rather than purely local storage.

### Likelihood Explanation
Requires an integrator to feed attacker-influenced bytes into `ThresholdKeys::read` — plausible wherever serialized keys are exchanged or recovered rather than produced by a local honest DKG. The defect is deterministic: no races or negligible probabilities. It is a missing-validation bug of exactly the reported class.

### Recommendation
- In `ThresholdKeys::read`, call the identity-rejecting reader (or explicitly reject `is_identity`) for each verification share and for the resulting `group_key`/`secret_share == ZERO`.
- Alternatively/additionally, have `ThresholdKeys::new` reject identity verification shares and an identity `group_key` so all constructors share the invariant.

### Proof of Concept
```rust
use zeroize::Zeroizing;
use ciphersuite::{Ciphersuite, group::GroupEncoding};
use dkg::{ThresholdKeys, Participant, Interpolation};
use schnorr::SchnorrSignature;
use dalek_ff_group::{Ristretto, EdwardsPoint, Scalar};
use group::{Group, ff::PrimeField};

// Forge serialized ThresholdKeys: t = n = i = 1, Lagrange, share = 0, vshare = identity
let mut buf = vec![];
buf.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
buf.extend(Ristretto::ID);
buf.extend(1u16.to_le_bytes()); // t
buf.extend(1u16.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes()); // i
buf.push(1);                    // Interpolation::Lagrange
buf.extend(Scalar::ZERO.to_repr());           // secret share = 0 — accepted
buf.extend(EdwardsPoint::identity().to_bytes()); // verification share = identity — accepted

let keys = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice()).unwrap();
assert!(bool::from(keys.group_key().is_identity())); // group key is identity

// Universal forgery: any s gives a valid "signature" for this group key,
// since verify checks sG == R + c*I == R.
for s in [Scalar::ONE, Scalar::from(42u64)] {
  let sig = SchnorrSignature::<Ristretto> { R: EdwardsPoint::generator() * s, s };
  for c in [Scalar::ONE, Scalar::from(7u64)] {
    assert!(sig.verify(keys.group_key(), c));
  }
}
```

This produces a `ThresholdKeys` whose `group_key` is the identity point, under which arbitrarily constructed `(R = s·G, s)` pairs verify as valid Schnorr signatures for any challenge/message — a forged-signature condition caused by the missing non-identity/non-zero checks during deserialization.