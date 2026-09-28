### Title
Malformed `ThresholdKeys` deserialization creates an unspendable group key - (File: crypto/dkg/src/lib.rs)

### Summary

`ThresholdKeys::read` accepts a serialized secret share and serialized verification shares without checking that the secret share corresponds to the caller’s verification share. A maliciously constructed key file can therefore cause `group_key()`—and consequently a Bitcoin receive address—to be derived from attacker-selected verification shares while signing uses an unrelated secret share. Funds received at the resulting address are not spendable by the deserialized key.

### Finding Description

`ThresholdKeys::read` parses `t`, `n`, `i`, the interpolation mode, one scalar as `secret_share`, and `n` group elements as `verification_shares`, then passes them to `ThresholdKeys::new`. [1](#0-0)  `ThresholdKeys::new` checks only parameter/cardinality/interpolation applicability and derives `group_key` from the first `t` verification shares. [2](#0-1)  It never verifies that `C::generator() * secret_share == verification_shares[i]`.

During signing, `ThresholdKeys::view` interpolates the serialized secret share for the local participant. [3](#0-2)  The Bitcoin wallet derives the expected output script from `keys.group_key()` and rejects the spend only if the group-key-derived script differs from the received output’s script. [4](#0-3)  Thus a mismatched serialized key produces an address under the verification shares while signatures are produced with the unrelated scalar.

### Impact Explanation

An attacker who causes a victim to load a crafted serialized `ThresholdKeys` file can make the wallet display or operate an attacker-controlled or simply inconsistent threshold group key. If the verification shares define key `A` while the secret share is `s` where `sG != verification_shares[i]`, deposits can be received to the key derived from `A`, but the locally held scalar cannot produce valid shares for that key. This is loss of availability/integrity of wallet funds, and in the worst case can direct deposits to a key controlled solely by the supplier of the malicious file.

### Likelihood Explanation

Exploitation requires the victim or an integrating service to deserialize an attacker-supplied key file or backup through `ThresholdKeys::read`, analogous to opening a malicious serialized object. No malformed scalar or point encoding is needed: all fields can be canonical, so `C::read_F` and `C::read_G` succeed. [5](#0-4)  The parser also accepts the resulting object as structurally valid because `ThresholdKeys::new` performs no share-to-verification-share consistency check. [6](#0-5) 

### Recommendation

During `ThresholdKeys::read` and `ThresholdKeys::new`, reject keys unless:

```text
C::generator() * secret_share == verification_shares[params.i()]
```

This check is necessary but not sufficient for arbitrary `t < n`: consumers should also only deserialize threshold keys from trusted storage or authenticate the serialized object. For DKG-produced keys, the consistency relation should be established during DKG completion and preserved by authenticated persistence.

### Proof of Concept

For a one-of-one `Secp256k1` key, serialize:

```text
curve ID             = valid Secp256k1 ID
t                    = 1
n                    = 1
i                    = 1
interpolation        = Lagrange
secret_share         = 1
verification_shares  = { 1: 2G }
```

`ThresholdKeys::read` accepts this object because every scalar and point is canonical and `ThresholdParams::new(1, 1, 1)` is valid. [1](#0-0)  `ThresholdKeys::new` derives `group_key = 2G` from participant `1`’s verification share. [7](#0-6)  Signing nevertheless uses `secret_share = 1`, which corresponds to `G`, not `2G`. [3](#0-2) 

A Bitcoin scanner or transaction builder using `group_key()` reports outputs for the `2G`-derived Taproot script, while the signing machine uses scalar `1`, making the reported output unspendable by this key file. [4](#0-3)

### Citations

**File:** crypto/dkg/src/lib.rs (L349-389)
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
```

**File:** crypto/dkg/src/lib.rs (L493-508)
```rust
    // The interpolation occurs multiplicatively, letting us scale by the scalar now
    let secret_share_scaled = Zeroizing::new(self.scalar * self.original_secret_share().deref());
    let mut secret_share = Zeroizing::new(
      self.core.interpolation.interpolation_factor(self.params().i(), &included) *
        secret_share_scaled.deref(),
    );

    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
    }

```

**File:** crypto/dkg/src/lib.rs (L591-631)
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
    )
    .map_err(io::Error::other)
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

**File:** crypto/ciphersuite/src/lib.rs (L74-101)
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
  }
```
