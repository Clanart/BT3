### Title
ThresholdKeys deserialization accepts a zero secret and identity verification share, enabling signatures for a known group key - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::read` accepts a semantically invalid threshold key containing a zero `secret_share` and identity verification share. `ThresholdKeys::new` validates counts and participant bounds but never rejects zero/identity key material or verifies that `generator * secret_share` equals the local verification share. For a `1-of-1` key, this produces an identity `group_key` backed by the publicly known secret `0`, and the FROST Schnorr machine will emit a valid signature for that key.

### Finding Description
`ThresholdKeys::read` parses `t`, `n`, `i`, the interpolation mode, an arbitrary canonical scalar as `secret_share`, and `n` canonical points as verification shares. [1](#0-0)  The generic `Ciphersuite::read_G` parser validates canonical point encoding but does not reject the identity point. [2](#0-1) 

`ThresholdKeys::new` checks the amount and indexes of verification shares, then derives `group_key` from participants `1..=t`; it performs no nonzero check and no correspondence check between `secret_share` and `verification_shares[i]`. [3](#0-2) 

For `t = n = i = 1` with `secret_share = 0` and `verification_shares[1] = identity`, `group_key` is identity and the one-participant view leaves the secret share as zero. [4](#0-3)  The Schnorr algorithm then signs `s = r + c * 0 = r`, and the final verifier accepts because `sG == R` when the group key is identity. [5](#0-4) [6](#0-5) 

### Impact Explanation
If an attacker can supply or replace serialized `ThresholdKeys`, they can cause Serai code to operate on a threshold public key whose discrete logarithm is publicly known. The resulting FROST Schnorr signature is cryptographically valid for the identity group key, so downstream authorization that trusts that group key can be bypassed. More generally, malformed shares can make participants sign under group-key/share relationships that were never produced by the DKG.

### Likelihood Explanation
The attack requires untrusted bytes to reach `ThresholdKeys::read` and the resulting object to be accepted as a signing/authorization key. That is the reachable deserialization path specified for this API. No protocol participant, validator compromise, brute force, or leaked secret is required; the deserialized key itself encodes the degenerate zero secret.

### Recommendation
In `ThresholdKeys::new`, reject zero `secret_share` and identity verification shares, and require `C::generator() * secret_share == verification_shares[i]`. Parsing should also reject any set whose derived `group_key` is identity. These checks should occur before storing or returning the `ThresholdKeys`.

### Proof of Concept
Construct a serialized `ThresholdKeys<C>` with:

```text
C::ID length and ID
t = 1
n = 1
i = 1
interpolation = Lagrange (tag 1)
secret_share = 0
verification_shares[1] = identity
```

`ThresholdKeys::<C>::read` returns this object because both `0` and identity are accepted by the scalar/point readers. [1](#0-0) [2](#0-1) 

Instantiate `AlgorithmMachine::new(Schnorr::new(...), keys)`, run `preprocess`, call `sign` with no remote preprocesses, then call `complete` with no remote shares. The derived group key is identity, the interpolated secret is zero, and the emitted Schnorr signature satisfies `R + c*identity - sG = identity`. [7](#0-6) [8](#0-7)

### Citations

**File:** crypto/dkg/src/lib.rs (L349-379)
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

```

**File:** crypto/dkg/src/lib.rs (L493-522)
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

**File:** crypto/dkg/src/lib.rs (L591-630)
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

**File:** crypto/schnorr/src/lib.rs (L88-109)
```rust
  pub fn batch_statements(&self, public_key: C::G, challenge: C::F) -> [(C::F, C::G); 3] {
    // s = r + ca
    // sG == R + cA
    // R + cA - sG == 0
    [
      // R
      (C::F::ONE, self.R),
      // cA
      (challenge, public_key),
      // -sG
      (-self.s, C::generator()),
    ]
  }

  /// Verify a Schnorr signature for the given key with the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[must_use]
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
```

**File:** crypto/frost/src/sign.rs (L398-410)
```rust
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

**File:** crypto/frost/src/sign.rs (L454-466)
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
```
