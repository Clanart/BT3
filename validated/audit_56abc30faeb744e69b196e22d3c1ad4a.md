### Title
Ed448 deserialization accepts order-4 points, enabling forged Schnorr proofs - (File: crypto/ed448/src/point.rs)

### Summary
Ed448 point decoding intended to reject non-prime-subgroup points, but `Point::is_torsion_free` evaluates `-P + P`, which is always the identity and therefore accepts every otherwise-valid point encoding. [1](#0-0) 

### Finding Description
`Ciphersuite::read_G` trusts `GroupEncoding::from_bytes` followed by a canonical round-trip check, so an Ed448 point that passes `Point::from_bytes` is exposed to all deserialization users. [2](#0-1)  The encoded point with `y = 0` and either sign bit produces `x = ±1`; on Edwards448 these are non-identity order-4 points, but the tautological torsion check accepts them. [3](#0-2) [4](#0-3) 

PedPoP reaches this decoder through `Commitments::read`, which accepts attacker-controlled commitment points and a Schnorr proof from serialized bytes. [5](#0-4)  Verification queues the standard Schnorr statement `R + cA - sG = 0` for each participant. [6](#0-5) [7](#0-6) 

### Impact Explanation
An Ed448 participant can use an order-4 commitment point `A = T`, a signature with `R` equal to the identity, and `s = 0`; the statement reduces to `cT = 0` and verifies whenever the challenge is divisible by 4. [7](#0-6)  Because the challenge binds the nonce and all serialized commitments, an attacker can grind another commitment encoding until the scalar challenge has the required residue modulo 4. [8](#0-7)  This produces a valid-looking Schnorr proof/PoK for a point outside the prime-order subgroup and therefore without a corresponding prime-subgroup discrete logarithm. [9](#0-8) 

### Likelihood Explanation
The malicious bytes are fixed-size public DKG commitment messages, and no private key or validator privilege is required. [5](#0-4)  For a uniformly distributed scalar challenge, the required `c mod 4` condition occurs for roughly one quarter of candidates, and the attacker can vary committed bytes included in the challenge until it is met. [8](#0-7) [10](#0-9) 

### Recommendation
Implement `is_torsion_free` as an actual prime-order subgroup check, such as accepting `P` only when `qP` is the identity for the Ed448 prime subgroup order `q`, rather than checking `-P + P`. [11](#0-10)  Also add regression vectors covering the canonical encodings of `(±1, 0)`, the order-2 point, and the identity, ensuring all non-prime-subgroup points are rejected during `read_G`. [4](#0-3) 

### Proof of Concept
The following demonstrates that a malformed Ed448 encoding is accepted and that the resulting order-4 point satisfies the Schnorr verifier with an identity nonce and zero response when `c = 4`.

```rust
// crypto/ed448/src/point.rs
use group::{Group, GroupEncoding};
use ff::Field;
use ciphersuite::Ciphersuite;
use schnorr::SchnorrSignature;
use ed448::{Ed448, Point, Scalar};

let mut encoding = <Point as GroupEncoding>::Repr::default();

// y = 0 with the sign bit set; this represents an order-4 point.
encoding.as_mut()[56] = 0x80;

let torsion =
  <Ed448 as Ciphersuite>::read_G(&mut encoding.as_ref()).unwrap();

assert!(!bool::from(torsion.is_identity()));
assert!(bool::from(torsion.double().double().is_identity()));

let forged = SchnorrSignature::<Ed448> {
  R: Point::identity(),
  s: Scalar::ZERO,
};

assert!(forged.verify(torsion, Scalar::from(4)));
```

In the PedPoP path, the attacker chooses the order-4 point as the first commitment and varies a later commitment so the Fiat-Shamir challenge is divisible by 4, causing the same `(R = identity, s = 0)` proof to pass batch verification. [6](#0-5)

### Citations

**File:** crypto/ed448/src/point.rs (L203-214)
```rust
  fn double(&self) -> Self {
    // 7 muls, 7 additions, 4 negations
    let xsq = self.x.square();
    let ysq = self.y.square();
    let zsq = self.z.square();
    let xy = self.x + self.y;
    #[allow(non_snake_case)]
    let F = xsq + ysq;
    #[allow(non_snake_case)]
    let J = F - zsq.double();
    Point { x: J * (xy.square() - xsq - ysq), y: F * (xsq - ysq), z: F * J }
  }
```

**File:** crypto/ed448/src/point.rs (L294-317)
```rust
impl Point {
  fn is_torsion_free(&self) -> Choice {
    ((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()
  }
}

impl GroupEncoding for Point {
  type Repr = <FieldElement as PrimeField>::Repr;

  fn from_bytes(bytes: &Self::Repr) -> CtOption<Self> {
    // Extract and clear the sign bit
    let sign = Choice::from(bytes[56] >> 7);
    let mut bytes = *bytes;
    let mut_ref: &mut [u8] = bytes.as_mut();
    mut_ref[56] &= !(1 << 7);

    // Parse y, recover x
    FieldElement::from_repr(bytes).and_then(|y| {
      recover_x(y).and_then(|mut x| {
        x.conditional_negate(x.is_odd().ct_eq(&!sign));
        let not_negative_zero = !(x.is_zero() & sign);
        let point = Point { x, y, z: FieldElement::ONE };
        CtOption::new(point, not_negative_zero & point.is_torsion_free())
      })
```

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L85-93)
```rust
#[allow(non_snake_case)]
fn challenge<C: Ciphersuite>(context: [u8; 32], l: Participant, R: &[u8], Am: &[u8]) -> C::F {
  let mut transcript = RecommendedTranscript::new(b"DKG PedPoP v0.2");
  transcript.domain_separate(b"schnorr_proof_of_knowledge");
  transcript.append_message(b"context", context);
  transcript.append_message(b"participant", l.to_bytes());
  transcript.append_message(b"nonce", R);
  transcript.append_message(b"commitments", Am);
  C::hash_to_F(b"DKG-PedPoP-proof_of_knowledge-0", &transcript.challenge(b"schnorr"))
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

**File:** crypto/dkg/pedpop/src/lib.rs (L321-334)
```rust
      // Step 5: Validate each proof of knowledge
      // This is solely the prep step for the latter batch verification
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );

      commitments.insert(l, msg.commitments.drain(..).collect::<Vec<_>>());
    }

    batch.verify_vartime_with_vartime_blame().map_err(PedPoPError::InvalidCommitments)?;
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

**File:** crypto/ed448/src/ciphersuite.rs (L71-73)
```rust
  fn hash_to_F(dst: &[u8], data: &[u8]) -> Self::F {
    Scalar::wide_reduce(Self::H::digest([dst, data].concat()).as_ref().try_into().unwrap())
  }
```
