### Title
Malformed serialized threshold keys can crash Bitcoin FROST signing via an identity group key - (File: networks/bitcoin/src/crypto.rs)

### Summary
`ThresholdKeys::read` accepts caller-controlled threshold parameters, interpolation coefficients, a secret share, and verification shares before constructing `ThresholdKeys` [1](#0-0) . Because `read_F` accepts the canonical zero scalar and `read_G` performs canonical-decoding checks without rejecting the identity point, a syntactically valid serialization can describe a consistent 1-of-1 key whose reconstruction coefficient, secret share, verification share, and resulting group key are all zero/identity [2](#0-1) [3](#0-2) . During Bitcoin Schnorr signing, `Schnorr::sign_share` passes that group key to `Hram::hram` [4](#0-3) . `Hram::hram` unconditionally extracts the x-coordinate of `A`, while `x` panics when the point has no x-coordinate because it is infinity [5](#0-4) [6](#0-5) .

### Finding Description
The secp256k1 `ThresholdKeys` serialization contains, in order:

1. The curve-ID length and `b"secp256k1"`.
2. `t`, `n`, and participant index `i`.
3. An interpolation selector.
4. For `Constant`, `n` scalar coefficients.
5. The secret-share scalar.
6. `n` verification-share points [7](#0-6) .

Those fields are passed to `ThresholdParams::new` and `ThresholdKeys::new`, with deserialization errors converted to `io::Error` rather than panics [8](#0-7) . However, the primitive readers do not impose nonzero semantic constraints: `read_F` only checks `from_repr`, which accepts zero, and `read_G` only checks successful decoding and canonical re-encoding, which does not exclude the identity element [2](#0-1) [3](#0-2) .

For a 1-of-1 `Constant` key, an attacker can therefore encode coefficient `0`, secret share `0`, and verification share `G * 0 = identity`. This represents the mathematically consistent all-zero threshold key. When that key is used with `networks/bitcoin/src/crypto.rs`'s BIP-340 `Schnorr` algorithm, signing calls `Hram::hram(R, A, msg)` where `A` is the group key [4](#0-3) . `Hram::hram` calls `x(A)` to obtain the BIP-340 x-only encoding [5](#0-4) . For the point at infinity, `to_encoded_point(true).x()` returns `None`, and `expect("point at infinity")` terminates the process or thread [6](#0-5) .

### Impact Explanation
Malformed bytes accepted by `ThresholdKeys::read` can cause an unhandled panic during signing. In a node, wallet service, or signing component that treats serialized key material as untrusted protocol input, this produces a denial of service rather than an ordinary deserialization or signing error [9](#0-8) [10](#0-9) .

### Likelihood Explanation
The trigger does not require corrupting an internal key in memory: the hostile condition can be encoded directly in the supported `ThresholdKeys` serialization. The required values are trivial to construct because zero is a canonical scalar and the identity is a canonical group encoding [11](#0-10) . Exploitation requires the application to deserialize attacker-controlled `ThresholdKeys` and use them with the Bitcoin Schnorr `Algorithm`; applications that only deserialize trusted local key material are not exposed through this exact path.

### Recommendation
Reject degenerate threshold keys before they reach signing:

- Reject zero secret shares and identity verification shares in `ThresholdKeys::read` or `ThresholdKeys::new`.
- Reject an identity group key after interpolation/reconstruction.
- Return a deserialization or key-validation error instead of allowing the identity to reach `Hram::hram`.
- Independently make `Hram::hram` or `x` return an error-compatible result rather than panic, or ensure the FROST algorithm validates `group_key` before invoking it [4](#0-3) [12](#0-11) .

### Proof of Concept
Conceptual Rust trigger using the supported serialization format:

```rust
use group::{Group, GroupEncoding};
use group::ff::PrimeField;
use k256::{ProjectivePoint, Scalar};
use frost::{
  curve::Secp256k1,
  sign::{AlgorithmMachine, SignMachine},
  ThresholdKeys,
};
use bitcoin_serai::crypto::Schnorr;

let mut encoded = Vec::new();

// Curve ID.
encoded.extend_from_slice(&9u32.to_le_bytes());
encoded.extend_from_slice(b"secp256k1");

// ThresholdParams: t = 1, n = 1, i = 1.
encoded.extend_from_slice(&1u16.to_le_bytes());
encoded.extend_from_slice(&1u16.to_le_bytes());
encoded.extend_from_slice(&1u16.to_le_bytes());

// Interpolation::Constant.
encoded.push(0);

// One constant reconstruction coefficient: zero.
encoded.extend_from_slice(Scalar::ZERO.to_repr().as_ref());

// Secret share: zero.
encoded.extend_from_slice(Scalar::ZERO.to_repr().as_ref());

// Verification share for participant 1: G * 0 = identity.
encoded.extend_from_slice(ProjectivePoint::IDENTITY.to_bytes().as_ref());

let keys = ThresholdKeys::<Secp256k1>::read(&mut encoded.as_slice())
  .expect("malformed all-zero key deserialized");

let (machine, _) = AlgorithmMachine::new(Schnorr::new(), keys)
  .preprocess(&mut rand_core::OsRng);

// Panics in bitcoin_serai::crypto::x when Hram tries to extract x(A)
// for the identity group key.
let _ = machine.sign(Default::default(), b"message");
```

The panic path is `Algorithm::sign_share` → `Hram::hram` → `x(A)` → `expect("point at infinity")` [4](#0-3) [5](#0-4) [6](#0-5) .

### Citations

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

**File:** crypto/ciphersuite/src/lib.rs (L74-100)
```rust
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
  }

  /// Read a canonical point from something implementing std::io::Read.
  ///
  /// The provided implementation is safe so long as `GroupEncoding::to_bytes` always returns a
  /// canonical serialization.
  #[cfg(any(feature = "alloc", feature = "std"))]
  #[allow(non_snake_case)]
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

**File:** networks/bitcoin/src/crypto.rs (L10-23)
```rust
/// Get the x coordinate of a non-infinity point.
///
/// Panics on invalid input.
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}

/// Convert a non-infinity point to a XOnlyPublicKey (dropping its sign).
///
/// Panics on invalid input.
pub(crate) fn x_only(key: &ProjectivePoint) -> XOnlyPublicKey {
  XOnlyPublicKey::from_slice(&x(key)).expect("x_only was passed a point which was infinity or odd")
}
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
