### Title
Unauthenticated `ThresholdKeys` deserialization permits replacement of signing configuration with attacker-controlled keys - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::read` accepts serialized threshold parameters, a secret share, and verification shares, then constructs usable signing keys without authenticating that the serialized state belongs to the expected session or key. An attacker who can feed these bytes into a `ThresholdKeys::read` call can therefore replace the configured threshold signing identity with a key pair they fully control.

### Finding Description
The deserializer reads attacker-controlled `t`, `n`, participant index, interpolation mode, `secret_share`, and all `n` verification shares, then passes them directly to `ThresholdKeys::new`. [1](#0-0)  `ThresholdKeys::new` checks only the verification-share count, participant bounds, and interpolation applicability; it then derives `group_key` from the supplied verification shares. [2](#0-1) 

A `t = n = i = 1` payload is internally valid: the attacker chooses secret `x`, supplies `x * G` as the sole verification share, and obtains keys whose `group_key` is `x * G` and whose signing secret is `x`. During FROST signing, `view` interpolates the supplied secret share and `Schnorr::sign_share` signs with `params.secret_share()`. [3](#0-2) [4](#0-3) 

The completed signature is verified against `self.view.group_key()`, which is also derived from the attacker-supplied verification shares, so the replacement is self-consistent. [5](#0-4) 

### Impact Explanation
This is a full signing-key takeover on any path where an unprivileged party controls bytes passed to `ThresholdKeys::read`. The affected component will emit valid signatures under the attacker-selected group key rather than the previously configured threshold key. For wallet flows, `SignableTransaction::multisig` uses the supplied `ThresholdKeys` to derive the expected output script and construct the FROST machines, allowing signing behavior to be redirected to attacker-controlled key material. [6](#0-5) 

### Likelihood Explanation
The attack requires the attacker to cause a serialized key blob to be installed, restored, migrated, imported, or otherwise supplied to `ThresholdKeys::read`; it does not require any existing key share, peer privilege, or DKG participation. The serialized format is fully described by `write`, including curve ID, threshold parameters, interpolation, secret share, and verification shares. [7](#0-6)  The only substantive prerequisite is an unauthenticated write/import path that reaches this deserializer.

### Recommendation
Authenticate serialized `ThresholdKeys` before accepting them and bind the blob to the expected session, validator identity, public group key, and storage/provisioning domain. At construction, verify `verification_shares[params.i()] == C::generator() * secret_share`, and reject key blobs whose resulting group key does not match the expected authorized group key. Signing entry points should also require an authenticated expected key identity rather than trusting embedded serialized parameters.

### Proof of Concept

```rust
// Generic over a supported ciphersuite C.

// Attacker-selected 1-of-1 secret.
let x = C::random_nonzero_F(&mut OsRng);
let attacker_key = C::generator() * x;

// ThresholdKeys serialization:
// ID length || C::ID || t=1 || n=1 || i=1 || Lagrange || x || x*G
let mut bytes = Vec::new();
bytes.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
bytes.extend(C::ID);
bytes.extend(1u16.to_le_bytes()); // t
bytes.extend(1u16.to_le_bytes()); // n
bytes.extend(1u16.to_le_bytes()); // i
bytes.push(1);                    // Interpolation::Lagrange
bytes.extend(x.to_repr().as_ref());
bytes.extend(attacker_key.to_bytes().as_ref());

let keys = ThresholdKeys::<C>::read(&mut bytes.as_slice()).unwrap();

assert_eq!(keys.params().t(), 1);
assert_eq!(keys.params().n(), 1);
assert_eq!(keys.params().i(), Participant::new(1).unwrap());
assert_eq!(keys.original_secret_share().deref(), &x);
assert_eq!(keys.group_key(), attacker_key);

// The injected object can now be passed to AlgorithmMachine::new and sign
// under attacker_key without participation from the original threshold set.
```

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

**File:** crypto/dkg/src/lib.rs (L493-521)
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
```

**File:** crypto/dkg/src/lib.rs (L535-561)
```rust
  /// Write these keys to a type satisfying `std::io::Write`.
  ///
  /// This will not include the ephemeral scalar/offset.
  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(&u32::try_from(C::ID.len()).unwrap().to_le_bytes())?;
    writer.write_all(C::ID)?;
    writer.write_all(&self.core.params.t.to_le_bytes())?;
    writer.write_all(&self.core.params.n.to_le_bytes())?;
    writer.write_all(&self.core.params.i.to_bytes())?;
    match &self.core.interpolation {
      Interpolation::Constant(c) => {
        writer.write_all(&[0])?;
        for c in c {
          writer.write_all(c.to_repr().as_ref())?;
        }
      }
      Interpolation::Lagrange => writer.write_all(&[1])?,
    };
    let mut share_bytes = self.core.secret_share.to_repr();
    writer.write_all(share_bytes.as_ref())?;
    share_bytes.as_mut().zeroize();
    for l in 1 ..= self.core.params.n {
      writer.write_all(
        self.core.verification_shares[&Participant::new(l).unwrap()].to_bytes().as_ref(),
      )?;
    }
    Ok(())
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

**File:** crypto/frost/src/algorithm.rs (L201-217)
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
  }
```

**File:** crypto/frost/src/sign.rs (L454-467)
```rust
    let mut responses = HashMap::new();
    responses.insert(params.i(), self.share);
    let mut sum = self.share;
    for (l, share) in shares.drain() {
      responses.insert(l, share.0);
      sum += share.0;
    }

    // Perform signature validation instead of individual share validation
    // For the success route, which should be much more frequent, this should be faster
    // It also acts as an integrity check of this library's signing function
    if let Some(sig) = self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum) {
      return Ok(sig);
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
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

    Some(TransactionMachine { tx: self, sigs })
```
