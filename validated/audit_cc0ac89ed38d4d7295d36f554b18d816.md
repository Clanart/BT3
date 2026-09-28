### Title
Malformed `ThresholdKeys` can create an identity group key and panic BIP-340 signing - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` accepts a syntactically valid threshold-key serialization whose interpolated verification shares sum to the group identity. `ThresholdKeys::new` calculates the group key but does not reject an identity result; when the resulting keys are used with the Bitcoin Schnorr algorithm, `Hram::hram` calls `x(A)`, which explicitly panics on a point at infinity. This creates an unprivileged-input denial-of-service path analogous to a malformed file triggering an out-of-bounds failure. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`ThresholdKeys::read` trusts the serialized `t`, `n`, participant index, interpolation mode, secret share, and `n` verification-share encodings before constructing a `ThresholdKeys` object. [4](#0-3) 

For Lagrange interpolation, participant `1` in a two-participant set has coefficient `2`, while participant `2` has coefficient `-1`. [5](#0-4) 

`ThresholdKeys::new` uses participants `1..=t` to compute the group key as the sum of `verification_shares[i] * interpolation_factor(i)`, but it does not check whether that sum is the identity. [2](#0-1) 

Consequently, for `t = n = 2`, an input containing verification shares `V1 = G` and `V2 = 2G` produces `2*V1 - V2 = 0`, an identity group key, while every encoded point and scalar can remain canonical. [6](#0-5) [2](#0-1) 

During FROST signing, `AlgorithmSignMachine::sign` calls the selected algorithm’s `sign_share` with the derived `ThresholdView`. [7](#0-6) 

The Bitcoin `Hram::hram` implementation hashes `x(R)`, `x(A)`, and the message; `x` panics when passed the point at infinity. [8](#0-7) [3](#0-2) 

The resulting behavior is a crash rather than an `Err` from threshold-key parsing or signing. [9](#0-8) [10](#0-9) 

### Impact Explanation
A service or signer that accepts serialized `ThresholdKeys` through `ThresholdKeys::read` can be forced to panic before producing a signature. [11](#0-10) 

Because Rust bounds and point checks prevent memory corruption, the demonstrated result is denial of service rather than secret-key recovery or signature forgery. [12](#0-11) [3](#0-2) 

The panic is deterministic once the malformed keys are loaded: the group key is semantically invalid but passes construction, then reaches a documented panic path in the Bitcoin Schnorr challenge code. [13](#0-12) [14](#0-13) 

### Likelihood Explanation
The malicious serialized object requires only canonical field and group encodings; neither participant shares nor scalar values need to violate their primitive encodings. [4](#0-3) [6](#0-5) 

The attacker only needs to arrange a linear cancellation among the verification shares, such as `V1 = G` and `V2 = 2G` for a two-of-two Lagrange key object. [5](#0-4) [2](#0-1) 

Exploitability is limited to callers that treat deserialized `ThresholdKeys` as untrusted input and then attempt signing with the Bitcoin Schnorr algorithm; the input does not by itself expose a secret or forge a signature. [11](#0-10) [10](#0-9) 

### Recommendation
Reject an identity `group_key` in `ThresholdKeys::new` after interpolation and before constructing `ThresholdKeys`. [13](#0-12) 

Also reject identity `verification_shares` at construction time unless identity is explicitly a supported semantic value. [15](#0-14) 

As defense in depth, make Bitcoin `x`/`x_only` return an error instead of panicking and propagate that error through `Hram::hram` and `sign_share`. [3](#0-2) [16](#0-15) 

Add a regression test deserializing a two-of-two `ThresholdKeys` whose verification shares are `G` and `2G`, and assert that deserialization or construction returns a semantic error rather than succeeding. [17](#0-16) 

### Proof of Concept
The serialized `ThresholdKeys<Secp256k1>` layout is:

```text
u32le length of Secp256k1::ID
Secp256k1::ID bytes
u16le t = 2
u16le n = 2
u16le i = 1
u8 interpolation = 1              // Lagrange
canonical scalar secret_share     // any canonical scalar
canonical compressed V1 = G
canonical compressed V2 = 2*G
```

The Lagrange factors for included participants `[1, 2]` are `2` and `-1`, so the reconstructed group key is `2G - 2G = identity`. [5](#0-4) 

`ThresholdKeys::read` accepts those values and passes them to `ThresholdKeys::new`, which stores the identity group key without rejecting it. [17](#0-16) 

A minimal triggering sequence is:

```rust
let keys: ThresholdKeys<Secp256k1> = ThresholdKeys::read(&mut malicious_bytes)?;
let (machine, our_preprocess) =
    AlgorithmMachine::new(Schnorr::new(), keys).preprocess(&mut rng);

// Include the local participant and one other participant's syntactically valid preprocess.
let mut preprocesses = HashMap::new();
preprocesses.insert(Participant::new(2).unwrap(), other_preprocess);

// Panics in BIP-340 Hram when x(A) receives the identity group key.
let _ = machine.sign(preprocesses, b"message");
```

The signing path reaches `Hram::hram` through `sign_share`, and `x(A)` panics because `A` is the identity group key. [10](#0-9) [16](#0-15) [3](#0-2)

### Citations

**File:** crypto/dkg/src/lib.rs (L224-246)
```rust
impl<F: Zeroize + PrimeField> Interpolation<F> {
  /// The interpolation factor for this participant, within this signing set.
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
```

**File:** crypto/dkg/src/lib.rs (L355-365)
```rust
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
```

**File:** crypto/dkg/src/lib.rs (L376-390)
```rust
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

**File:** networks/bitcoin/src/crypto.rs (L50-72)
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
```

**File:** crypto/ciphersuite/src/lib.rs (L71-100)
```rust
  /// Read a canonical scalar from something implementing std::io::Read.
  #[cfg(any(feature = "alloc", feature = "std"))]
  #[allow(non_snake_case)]
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

**File:** crypto/frost/src/sign.rs (L382-410)
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

    Ok((
      AlgorithmSignatureMachine {
        params: self.params,
        view,
        B,
        Rs,
        share,
        blame_entropy: self.blame_entropy,
      },
      SignatureShare(share),
    ))
```

**File:** crypto/frost/src/algorithm.rs (L201-211)
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
  }
```
