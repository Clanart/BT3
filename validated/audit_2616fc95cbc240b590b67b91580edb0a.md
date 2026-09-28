### Title
Unvalidated serialized `ThresholdKeys` make scanned funds unspendable - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts an arbitrary secret share and independent verification shares without checking that the local secret corresponds to `verification_shares[i]`, allowing attacker-supplied bytes to create a key that advertises a spendable group key but cannot produce a valid signature. [1](#0-0) 

### Finding Description
`ThresholdKeys::read` deserializes `t`, `n`, `i`, the interpolation mode, a `secret_share`, and `n` verification shares before passing them to `ThresholdKeys::new`. [2](#0-1) 

`ThresholdKeys::new` checks only that the verification-share count equals `n`, participant indices are within range, and constant interpolation is restricted to `t == n`. [3](#0-2) 

It derives `group_key` solely from the first `t` verification-share points and stores the separately supplied `secret_share` without checking `verification_shares[i] == G * secret_share`. [4](#0-3) 

During signing, `view` derives the signing secret from the deserialized `secret_share`, while the same view advertises the independently derived `group_key`. [5](#0-4) 

For the Schnorr algorithm, the signature share is calculated as `r + c * secret_share`, while final verification is against `group_key`. [6](#0-5) 

Consequently, a serialized `ThresholdKeys` object can expose a valid-looking public key and be accepted, while every signature attempted with it is invalid. [7](#0-6) 

The Bitcoin wallet scanner then recognizes payments to that advertised group key and returns them as `ReceivedOutput`s, even though the deserialized key material cannot authorize their spend. [8](#0-7) [9](#0-8) 

### Impact Explanation
An attacker who can supply a serialized `ThresholdKeys` blob can cause the system to initialize wallet scanning and signing around an unrelated public group key. [1](#0-0) 

Funds sent to the reported Taproot key are surfaced as received outputs, but attempts to spend them produce invalid Schnorr signature shares rather than a valid transaction signature. [9](#0-8) [10](#0-9) 

For a `1-of-1` key, this is deterministic: the sole deserialized participant must sign and has no alternative signing set. [11](#0-10) 

### Likelihood Explanation
The issue is reachable through the explicitly untrusted `ThresholdKeys::read` byte format; no malformed curve point or privileged operation is required. [1](#0-0) 

An attacker can deterministically choose any public point `P` as participant `1`'s verification share while encoding `1` as the secret share, producing an accepted object whose public key cannot be signed for unless the attacker also reveals the discrete logarithm of `P`. [4](#0-3) 

### Recommendation
When constructing or deserializing `ThresholdKeys`, verify that `C::generator() * secret_share == verification_shares[params.i()]` and reject the object if the relation does not hold. [12](#0-11) 

The implementation should additionally validate that all advertised verification shares form a consistent threshold public-key set, rather than deriving `group_key` from an unchecked subset of attacker-controlled points. [13](#0-12) 

### Proof of Concept
The following conceptual serialization encodes a `1-of-1` key whose advertised group key is `victim_key`, while its actual signing secret is `1`. [14](#0-13) 

```rust
// For C = Secp256k1 and an even victim_key = G * x_victim:
let mut bytes = Vec::new();

bytes.extend((Secp256k1::ID.len() as u32).to_le_bytes());
bytes.extend(Secp256k1::ID);

bytes.extend(1u16.to_le_bytes()); // t
bytes.extend(1u16.to_le_bytes()); // n
bytes.extend(1u16.to_le_bytes()); // i
bytes.push(1);                    // Lagrange interpolation

bytes.extend(Scalar::ONE.to_repr());      // mismatched secret_share
bytes.extend(victim_key.to_bytes());      // verification_shares[1]

let keys = ThresholdKeys::<Secp256k1>::read(&mut bytes.as_slice()).unwrap();
assert_eq!(keys.group_key(), victim_key);
```

`Scanner::new(keys.group_key())` accepts an even key, and `scan_transaction` returns any payment to `victim_key` as a `ReceivedOutput`. [8](#0-7) [15](#0-14) 

The FROST signer calculates the sole share using secret `1`, while `complete` verifies it against `victim_key = G * x_victim`; unless `x_victim == 1`, the batch identifies participant `1`'s share as invalid and no signature is produced. [6](#0-5) [10](#0-9)

### Citations

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

**File:** crypto/dkg/src/lib.rs (L493-532)
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

    /*
      The offset is included by adding it to the participant with the lowest ID.

      This is done after interpolating to ensure, regardless of the method of interpolation, that
      the method of interpolation does not scale the offset. For Lagrange interpolation, we could
      add the offset to every key share before interpolating, yet for Constant interpolation, we
      _have_ to add it as we do here (which also works even when we intend to perform Lagrange
      interpolation).
    */
    if included[0] == self.params().i() {
      *secret_share += self.offset;
    }
    *verification_shares.get_mut(&included[0]).unwrap() += C::generator() * self.offset;

    Ok(ThresholdView {
      interpolation: self.core.interpolation.clone(),
      scalar: self.scalar,
      offset: self.offset,
      group_key: self.group_key(),
      secret_share,
      original_verification_shares: self.core.verification_shares.clone(),
      verification_shares,
      included,
    })
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

**File:** crypto/frost/src/algorithm.rs (L201-216)
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

  #[must_use]
  fn verify(&self, group_key: C::G, nonces: &[Vec<C::G>], sum: C::F) -> Option<Self::Signature> {
    let sig = SchnorrSignature { R: nonces[0][0], s: sum };
    Some(sig).filter(|sig| sig.verify(group_key, self.c.unwrap()))
```

**File:** crypto/frost/src/sign.rs (L290-312)
```rust
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

**File:** crypto/frost/src/sign.rs (L465-494)
```rust
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

    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
```

**File:** networks/bitcoin/src/wallet/mod.rs (L162-165)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-213)
```rust
  /// Scan a transaction.
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
```
