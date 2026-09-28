### Title
BIP-340 `verify` validates the aggregate signature against the raw (possibly odd-parity) group key instead of the canonical even-Y x-only key, producing signatures Bitcoin consensus rejects - (File: networks/bitcoin/src/crypto.rs)

### Summary
The Size multicall bug class is "a validation routine reads the wrong variable, so the check silently passes while the actual invariant is broken" (`balanceOf(this)` instead of `totalSupply()`). The same shape exists in `bitcoin_serai`'s BIP-340 `Algorithm::verify`: it checks the Schnorr equation against the full `ProjectivePoint` `group_key` — parity included — while Bitcoin's BIP-340 verifier checks it against the *even-lift* of `x(group_key)` with an implicitly negated secret. When `group_key` has odd Y, `verify` returns `Some(sig)` and `complete` succeeds, yet the emitted 64-byte signature is invalid on-chain.

### Finding Description
`Schnorr::verify` delegates to `FrostSchnorr::verify`, which checks `sig.verify(group_key, c)` using the full point `group_key`: [1](#0-0) [2](#0-1) 

`Hram` correctly reduces the challenge to x-only (`x(R)`, `x(A)`) and negates `c` when `R` is odd, and `verify` conditionally negates `s` when `sig.R` is odd: [3](#0-2) [4](#0-3) 

But BIP-340 additionally requires the *secret key* `d` to be the even-Y representative: if `A = x·G` is odd, signing must use `-x` so the verifier's even lift of `x(A)` satisfies `s·G = R' + c·A_even`. `sign_share` uses `params.secret_share()` verbatim — the share of the raw key — and `verify` never applies `needs_negation(&group_key)`: [5](#0-4) 

So for an odd `group_key`, the library checks `s·G = R + c·A` (raw `A`), while consensus checks `s·G = R' + c·(-A)`. These differ by `2cA`, so a signature the library returns as valid fails BIP-340 verification. `tweak_keys` happens to normalize parity for its own outputs, but it is a separate, optional helper — nothing in `Schnorr`/`ThresholdKeys` enforces that the group key fed to `complete` is even, and `ThresholdKeys::read` accepts arbitrary serialized `verification_shares`/`offset` yielding an odd `group_key`: [6](#0-5) [7](#0-6) 

### Impact Explanation
An unprivileged party feeding crafted `ThresholdKeys::read` bytes (or any code path that applies `keys.offset(...)` / omits `tweak_keys` such that `group_key` is odd) causes `AlgorithmSignatureMachine::complete` to emit `Ok(signature)` that Bitcoin consensus rejects — analogous to the report's "check measured the wrong quantity so the invariant silently passed". Funds sent to the corresponding P2TR output are unspendable via the produced signature; a coordinator/processor believing the batch signature finalized would publish a transaction that fails relay/script verification. Per the rules this is "an incorrect verifier formula" — Medium.

### Likelihood Explanation
~50% of random keys are odd, so any integration path that skips `tweak_keys` (or applies a caller-supplied `offset` without re-normalizing parity) triggers it deterministically. Adversarial reachability exists via `ThresholdKeys::read`, an explicitly in-scope untrusted-bytes entry point, which reconstructs `group_key` from attacker-controlled `verification_shares` with no parity constraint.

### Recommendation
In `frost_crypto::Schnorr::verify` (or in `sign_share`/`Hram`), normalize the key parity: compute `needs_negation(&group_key)` and, if odd, verify/sign against `-group_key` (equivalently negate `sum`/`secret_share`), matching BIP-340's implicit secret negation — mirroring how `tweak_keys` already scales keys by `-1` for odd `group_key`. Alternatively, hard-fail in `verify`/`sign_share` when `group_key` is odd.

### Proof of Concept
1. Build `ThresholdKeys<Secp256k1>` (via `ThresholdKeys::new`/`read`, or `key_gen` then `keys.offset(d)`) such that `keys.group_key()` has odd Y — `needs_negation(&keys.group_key()) == 1`.
2. Run the FROST signing flow with `bitcoin_serai::crypto::Schnorr` over any `msg`; every participant is honest.
3. `complete` calls `Schnorr::verify`, which checks `sig.verify(group_key, c)` against the raw odd point and returns `Some(sig)`; `s` is only negated for `R`'s parity.
4. Verify `(x(R), s)` under BIP-340 with pubkey `x(group_key)`: the verifier computes `s·G - c·lift_x(x(A)) = R + 2cA ≠ lift_x(x(R))` — the signature is invalid on Bitcoin, despite `complete` reporting success.

### Citations

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

**File:** networks/bitcoin/src/crypto.rs (L138-150)
```rust
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
    }
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

**File:** crypto/frost/src/algorithm.rs (L213-217)
```rust
  #[must_use]
  fn verify(&self, group_key: C::G, nonces: &[Vec<C::G>], sum: C::F) -> Option<Self::Signature> {
    let sig = SchnorrSignature { R: nonces[0][0], s: sum };
    Some(sig).filter(|sig| sig.verify(group_key, self.c.unwrap()))
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L46-75)
```rust
pub fn tweak_keys(keys: ThresholdKeys<Secp256k1>) -> ThresholdKeys<Secp256k1> {
  // Adds the unspendable script path per
  // https://github.com/bitcoin/bips/blob/master/bip-0341.mediawiki#cite_note-23
  let keys = {
    use k256::elliptic_curve::{
      bigint::{Encoding, U256},
      ops::Reduce,
      group::GroupEncoding,
    };
    let tweak_hash = TapTweakHash::hash(&keys.group_key().to_bytes().as_slice()[1 ..]);
    /*
      https://github.com/bitcoin/bips/blob/master/bip-0340.mediawiki#cite_ref-13-0 states how the
      bias is negligible. This reduction shouldn't ever occur, yet if it did, the script path
      would be unusable due to a check the script path hash is less than the order. That doesn't
      impact us as we don't want the script path to be usable.
    */
    keys.offset(<Secp256k1 as Ciphersuite>::F::reduce(U256::from_be_bytes(
      *tweak_hash.to_raw_hash().as_ref(),
    )))
  };

  let needs_negation = needs_negation(&keys.group_key());
  keys
    .scale(<_ as subtle::ConditionallySelectable>::conditional_select(
      &Scalar::ONE,
      &-Scalar::ONE,
      needs_negation,
    ))
    .expect("scaling keys by 1 or -1 yet interpreted as 0?")
}
```

**File:** crypto/dkg/src/lib.rs (L574-632)
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
  }
```
