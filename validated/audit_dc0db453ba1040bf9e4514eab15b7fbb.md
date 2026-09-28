### Title
Malformed `ThresholdKeys` can create an identity Bitcoin group key and panic signing - ([File: crypto/dkg/src/lib.rs])

### Summary

`ThresholdKeys::read` accepts independently encoded verification shares and reconstructs `group_key` by interpolating the first `t` shares, but it never rejects an identity group key or verifies that the shares encode a consistent threshold polynomial. [1](#0-0) [2](#0-1) 

For `t = n = 2`, verification shares `G*s` and `G*2s` deserialize successfully while producing `2(G*s) - (G*2s) = identity`. [3](#0-2) 

### Finding Description

The vulnerable path is:

1. `ThresholdKeys::<Secp256k1>::read` reads arbitrary Lagrange parameters, a secret share, and verification shares.
2. `ThresholdKeys::new` validates the share count and indexes, then derives `group_key` from only participants `1..=t`.
3. It does not reject `group_key == identity`.
4. `SignableTransaction::multisig` calls `p2tr_script_buf(offset.group_key())`.
5. `p2tr_script_buf` calls `x_only`, which panics when the point has no x-coordinate—namely the point at infinity. [4](#0-3) [5](#0-4) [6](#0-5) 

The same invariant can be reached during FROST completion: each crafted participant share can individually verify against its corresponding verification share, while `Schnorr::verify` passes the identity aggregate key into `Hram::hram`; `Hram::hram` calls `x(A)`, whose documentation and implementation panic for infinity. [7](#0-6) [8](#0-7) 

### Impact Explanation

An unprivileged party supplying crafted `ThresholdKeys::read` bytes can make Bitcoin key handling abort rather than return an error. [9](#0-8) 

If this state reaches transaction construction, `SignableTransaction::multisig` panics while deriving the Taproot script. [10](#0-9) 

If it reaches signing completion, Bitcoin's challenge hash panics on the identity aggregate key before signature verification can fail cleanly. [11](#0-10) 

This is a Medium availability issue: it requires the application to accept attacker-controlled serialized threshold key material, but that input path is explicitly exposed through `ThresholdKeys::read`. [12](#0-11) 

### Likelihood Explanation

The trigger does not require finding a hash collision, guessing a discrete logarithm, or controlling multiple validators. [1](#0-0) 

For two-of-two Lagrange keys, choosing a nonzero scalar `s`, `V1 = G*s`, `V2 = G*2s`, `secret_share_1 = s`, and `secret_share_2 = 2s` produces an identity group key while retaining matching per-participant secret and verification shares. [3](#0-2) [13](#0-12) 

### Recommendation

Reject `group_key.is_identity()` in `ThresholdKeys::new`, immediately after interpolation. [14](#0-13) 

Also validate the deserialized secret share against `verification_shares[i]`, and preferably validate that arbitrary deserialized verification shares satisfy the expected threshold-sharing consistency rules rather than treating them as a trusted polynomial. [15](#0-14) 

Defensively, make `x`, `x_only`, `p2tr_script_buf`, and the Bitcoin `Algorithm::verify` path return an error/`None` for infinity instead of panicking. [6](#0-5) [5](#0-4) 

### Proof of Concept

Conceptual `Secp256k1` serialized-key construction:

```rust
// Parameters:
//   t = 2
//   n = 2
//   i = 1 or 2
//   interpolation = Lagrange
//
// Let s be any nonzero scalar:
//   V1 = G * s
//   V2 = G * 2s
//
// Participant 1 secret_share = s
// Participant 2 secret_share = 2s
//
// Lagrange factors at zero for included [1, 2] are:
//   lambda_1 = 2
//   lambda_2 = -1
//
// Therefore:
//   group_key = 2*V1 - V2
//             = 2*(G*s) - (G*2s)
//             = identity
```

Feed the corresponding byte encoding to `ThresholdKeys::<Secp256k1>::read`. The reader creates `n` verification-share entries and delegates validation to `ThresholdKeys::new`, which accepts the object because only individual encodings, participant indexes, interpolation applicability, and the share count are checked. [16](#0-15) 

Calling `keys.group_key()` then returns identity, and passing those keys to `SignableTransaction::multisig` reaches `p2tr_script_buf(identity)`, which panics in `x_only`/`x`. [10](#0-9) [6](#0-5)

### Citations

**File:** crypto/dkg/src/lib.rs (L229-247)
```rust
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

**File:** crypto/dkg/src/lib.rs (L573-632)
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
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-282)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L80-85)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
```

**File:** networks/bitcoin/src/crypto.rs (L13-23)
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
}
```

**File:** networks/bitcoin/src/crypto.rs (L54-67)
```rust
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
```

**File:** crypto/frost/src/sign.rs (L462-489)
```rust
    // Perform signature validation instead of individual share validation
    // For the success route, which should be much more frequent, this should be faster
    // It also acts as an integrity check of this library's signing function
    if let Some(sig) = self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum) {
      return Ok(sig);
    }

    // We could remove blame_entropy by taking in an RNG here
    // Considering we don't need any RNG for a valid signature, and we only use the RNG here for
    // performance reasons, it doesn't feel worthwhile to include as an argument to every
    // implementor of the trait
    let mut rng = ChaCha20Rng::from_seed(self.blame_entropy);
    let mut batch = BatchVerifier::new(self.view.included().len());
    for l in self.view.included() {
      if let Ok(statements) = self.params.algorithm.verify_share(
        self.view.verification_share(*l),
        &self.B.bound(*l),
        responses[l],
      ) {
        batch.queue(&mut rng, *l, statements);
      } else {
        Err(FrostError::InvalidShare(*l))?;
      }
    }

    if let Err(l) = batch.verify_vartime_with_vartime_blame() {
      Err(FrostError::InvalidShare(l))?;
    }
```
