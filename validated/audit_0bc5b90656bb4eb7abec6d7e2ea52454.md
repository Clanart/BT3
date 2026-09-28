### Title
Ed448 point deserialization accepts small-order torsion points due to no-op `is_torsion_free` check - (File: crypto/ed448/src/point.rs)

### Summary
Analogous to deserialization of untrusted data, `Ed448::read_G` (via `Ciphersuite::read_G` → `GroupEncoding::from_bytes`) accepts attacker-supplied point encodings that lie outside the prime-order subgroup. The `Point::is_torsion_free` check in `crypto/ed448/src/point.rs:295-297` computes `(*self * (Scalar::ZERO - Scalar::ONE)) + self`, i.e. `(-P) + P`, which is the identity for **every** point. The torsion check is therefore a no-op, and Ed448's cofactor-4 torsion components are silently accepted as valid `PrimeGroup` elements. This invalidates the `PrimeGroup` contract that `SchnorrSignature::verify`, `batch_statements`, PedPoP `Commitments`, `GeneratorProof`, and `DLEqProof` verification rely on for soundness.

### Finding Description
`Point::from_bytes` gates acceptance on `not_negative_zero & point.is_torsion_free()` (crypto/ed448/src/point.rs:303-319). But `is_torsion_free` is defined as:

```rust
fn is_torsion_free(&self) -> Choice {
  ((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()
}
```

`Scalar::ZERO - Scalar::ONE = -1`, so the expression is `P·(-1) + P = identity` unconditionally — for prime-order points and for order-2/order-4 torsion points alike. The file's own test (`torsion`, line 367) constructs the original torsion-ed Ed448 generator and asserts `!old.is_torsion_free()`, which now fails, confirming the check is broken. Upstream Serai performs this check via multiplication by the subgroup order (a scalar roughly 2^446); here the multiplier is just `-1`.

Because `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101) only checks `from_bytes` plus canonical re-encoding, every untrusted Ed448 point ingested through `read_G` — Schnorr `R` values and PoK points (`SchnorrSignature::read`, `SchnorrPoK::read`), PedPoP `Commitments::read` (crypto/dkg/pedpop/src/lib.rs:110-128), `EncryptedMessage::read` keys, `GeneratorProof::read` shares (crypto/dkg/promote/src/lib.rs:72-77), `ThresholdKeys::read` verification shares (crypto/dkg/src/lib.rs:620-623) — may carry a hidden torsion component.

### Impact Explanation
This enables **proof/signature forgery against any verifier that accepts an Ed448 public-key-like point from untrusted bytes**. Since `SchnorrSignature::verify` only checks the prime-order equation `sG == R + cA` (crypto/schnorr/src/lib.rs:88-110), a torsion component in `A` is invisible except through `c mod 4`. An attacker who publishes `A = a·G + T` (knowing `a`, while `A` has no discrete log in the prime subgroup) can produce a valid signature/PoK by grinding:

1. Pick nonce-scalar `x`, set `R' = x·G`, compute `ĉ = H(R', A)` and `T' = ĉ·T`.
2. Set `R = R' - T'`. Let `c = H(R, A)` be the real challenge.
3. If `c ≡ ĉ (mod 4)` (probability 1/4 per iteration, trivially grindable), then `R + cA = (x + c·a)·G + (c − ĉ)·T` lies in the prime subgroup with known dlog `s = x + c·a`. The signature `(R, s)` verifies under a key whose discrete logarithm does not exist.

Concretely in PedPoP, `Commitments::read` accepts `commitments[0] = a·G + T`; the embedded `SchnorrSignature` PoK is verified exactly via the equation above, so a party can pass proof-of-knowledge verification for a commitment coefficient whose true scalar is undefined mod the torsion. Downstream share/verification-share sums then absorb a live torsion component, corrupting group-key derivation and any verification built on it while every individual check passes.

### Likelihood Explanation
- Reachability: any unprivileged counterparty able to submit serialized Ed448 points (`Commitments`, `GeneratorProof`, `EncryptedMessage`, `SchnorrSignature`, `DLEqProof` inputs) reaches `from_bytes` with zero additional requirements.
- Exploit cost: finding a usable `R` requires ~4 challenge evaluations on average (the torsion subgroup has order 4), and the torsion encoding can be precomputed offline (e.g., the documented torsion generator at `point.rs:356-366` — which the broken test itself constructs — already provides a 57-byte encoding `from_bytes` will accept).
- Note: the crate is documented as "not recommended"/unaudited, but it is in-scope production code and is wired into FROST (`IetfEd448Hram`), the DKG, and the generic `Ciphersuite` read path, so integrators selecting it inherit the flaw.

### Recommendation
Replace `is_torsion_free` with a real subgroup-membership check: multiply by the prime subgroup order `l` (the `Scalar` field modulus defined in `crypto/ed448/src/scalar.rs:23`) and require the identity, e.g. `(self * Scalar(MODULUS-residue))` or equivalently check `P·l == identity`. Since `Scalar` arithmetic is mod `l`, the correct formulation is a scalar multiplication by a fixed constant representing `l` — practically, multiply by the cofactor `4` and confirm the result is *not* identity-confined incorrectly; the standard approach is `(P * l).is_identity()` implemented via an explicit bit-fiddled multiplication, or `P.double().double()` checked against `identity` only after also verifying `P * l` — simplest correct fix: add a `const SUBGROUP_ORDER` scalar bytes constant and compute `(self * order).is_identity()`. Also restore a passing `torsion` test asserting rejection inside `from_bytes` (not just the internal helper), plus a PoK-forgery regression test using `A = aG + T`.

### Proof of Concept
```rust
// crypto/ed448: demonstrates from_bytes accepts a torsion point.
// Uses the order-4 torsion point already encoded in the `torsion` test.
use minimal_ed448::{Point, Scalar};
use group::{Group, GroupEncoding};
use ff::Field;

// Constructed in the existing test: `old` is a valid curve point NOT in the
// prime-order subgroup.
let old_y = FieldElement::from_repr(/* bytes from test */).unwrap();
let torsion_point = Point { x: -recover_x(old_y).unwrap(), y: old_y, z: ONE };

// BUG: passes, because is_torsion_free() == (-P) + P == identity for all P.
assert!(bool::from(torsion_point.is_torsion_free())); // should be false
assert!(bool::from(Point::from_bytes(&torsion_point.to_bytes()).is_some()));

// Schnorr PoK forgery sketch (attacker knows `a`, key A = a*G + T has no dlog):
//   loop x: R' = x*G; ĉ = H(R', A); R = R' − ĉ*T;
//   c = hra(G, R, A); if c ≡ ĉ (mod 4): s = x + c*a  // verifies, ~4 tries avg
//   check: s*G − c*A − R = (x + ca)G − c(aG + T) − (xG − ĉT)
//        = (c − ĉ)*T = 0  when c ≡ ĉ (mod 4) since T has order dividing 4
```

Root cause: `crypto/ed448/src/point.rs:296` uses `-1` instead of the subgroup order as the torsion-check multiplier, making the `PrimeGroup` guarantee vacuous for every deserialized point. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crypto/ed448/src/point.rs (L294-298)
```rust
impl Point {
  fn is_torsion_free(&self) -> Choice {
    ((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()
  }
}
```

**File:** crypto/ed448/src/point.rs (L311-318)
```rust
    FieldElement::from_repr(bytes).and_then(|y| {
      recover_x(y).and_then(|mut x| {
        x.conditional_negate(x.is_odd().ct_eq(&!sign));
        let not_negative_zero = !(x.is_zero() & sign);
        let point = Point { x, y, z: FieldElement::ONE };
        CtOption::new(point, not_negative_zero & point.is_torsion_free())
      })
    })
```

**File:** crypto/ed448/src/point.rs (L351-368)
```rust
#[test]
fn torsion() {
  use generic_array::GenericArray;

  // Uses the originally suggested generator which had torsion
  let old_y = FieldElement::from_repr(*GenericArray::from_slice(
    &hex::decode(
      "\
12796c1532041525945f322e414d434467cfd5c57c9a9af2473b2775\
8c921c4828b277ca5f2891fc4f3d79afdf29a64c72fb28b59c16fa51\
00",
    )
    .unwrap(),
  ))
  .unwrap();
  let old = Point { x: -recover_x(old_y).unwrap(), y: old_y, z: FieldElement::ONE };
  assert!(bool::from(!old.is_torsion_free()));
}
```

**File:** crypto/ciphersuite/src/lib.rs (L91-101)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
  }
```

**File:** crypto/schnorr/src/lib.rs (L88-110)
```rust
  pub fn batch_statements(&self, public_key: C::G, challenge: C::F) -> [(C::F, C::G); 3] {
    // s = r + ca
    // sG == R + cA
    // R + cA - sG == 0
    [
      // R
      (C::F::ONE, self.R),
      // cA
      (challenge, public_key),
      // -sG
      (-self.s, C::generator()),
    ]
  }

  /// Verify a Schnorr signature for the given key with the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[must_use]
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L109-128)
```rust
impl<C: Ciphersuite> ReadWrite for Commitments<C> {
  fn read<R: Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    let mut commitments = Vec::with_capacity(params.t().into());
    let mut cached_msg = vec![];

    #[allow(non_snake_case)]
    let mut read_G = || -> io::Result<C::G> {
      let mut buf = <C::G as GroupEncoding>::Repr::default();
      reader.read_exact(buf.as_mut())?;
      let point = C::read_G(&mut buf.as_ref())?;
      cached_msg.extend(buf.as_ref());
      Ok(point)
    };

    for _ in 0 .. params.t() {
      commitments.push(read_G()?);
    }

    Ok(Commitments { commitments, cached_msg, sig: SchnorrSignature::read(reader)? })
  }
```
