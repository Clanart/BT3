### Title
Zero-value contributions accepted in PedPoP/DKG: identity commitments and zero secret shares yield a known (zero) group key - (File: crypto/dkg/pedpop/src/lib.rs, crypto/dkg/src/lib.rs)

### Summary
Analogous to a token that rejects a `0` approval the protocol unconditionally sends, Serai's DKG and `ThresholdKeys` construction unconditionally accept *zero* values — identity point commitments and a zero `secret_share` — that the protocol should reject but never checks. A participant (or attacker-supplied `ThresholdKeys` bytes via `ThresholdKeys::read`) can produce a `ThresholdKeys` whose interpolated `group_key` is the identity point, i.e. a threshold key whose secret is `0` — a key known to everyone, so any signature under it is forgeable and any funds attributed to it are unspendable-by-the-group / stealable-by-anyone.

### Finding Description

1. **PedPoP round 1 accepts identity commitments.** `verify_r1` only checks that `msg.commitments.len() == t` and batch-verifies the Schnorr PoK against `msg.commitments[0]` [1](#0-0) . The commitments are deserialized with `Ciphersuite::read_G`, which canonically decodes but does **not** reject the identity point [2](#0-1)  (contrast `Curve::read_G`, which does reject identity [3](#0-2) ). An identity `commitments[0]` has trivially-knowable discrete log `0`, so the required PoK still passes.

2. **Zero shares verify.** `calculate_share` accepts a decrypted share scalar of `0` (`from_repr` of all-zero bytes succeeds), and `share_verification_statements` verifies `Σ i^j·commitments[j] − share·G = identity`, which is satisfied by `share = 0` against all-identity commitments [4](#0-3) .

3. **Cancellation to a zero group key.** `verification_shares` are computed as `multiexp` over summed commitment stripes [5](#0-4) , and `ThresholdKeys::new` computes `group_key` as an interpolated sum of verification shares with **no identity check** [6](#0-5) . A participant who observes others' commitments before broadcasting their own (async delivery order is not enforced by the library) can choose `commitments[0] = −Σ others' commitments[0]`, and more generally commitments summing stripe-wise to values that make every verification share identity. The resulting `group_key` is `G·0` = identity — the group secret key is `0`, publicly known.

4. **Deserialization path.** `ThresholdKeys::read` reads `secret_share` via `C::read_F` (zero accepted) and each verification share via `<C as Ciphersuite>::read_G` (identity accepted), then calls the same unchecked `ThresholdKeys::new` [7](#0-6) . `scale`/`offset` can likewise drive `group_key = group_key·scalar + G·offset` to identity since `offset` accepts any scalar [8](#0-7) .

### Impact Explanation
A `ThresholdKeys` whose `group_key` is the identity has discrete log `0`. Any Schnorr/FROST signature under it verifies against a key everyone knows — an attacker can sign arbitrary messages ("concrete signing of an unintended message" / trivial key recovery). In the Bitcoin wallet path, outputs sent to such a group key are spendable by anyone (or, under BIP-340 where x-only encoding of infinity is invalid, the key is unusable — the direct analog of "BNB vaults won't work": an entire class of keys is silently broken despite the code claiming robustness). Like the original report, the failure is that a `0` value the protocol produces/consumes is never rejected where the spec implies it cannot occur.

### Likelihood Explanation
Medium. In the DKG setting it requires one malicious participant able to order its commitment message after seeing the others' (feasible over asynchronous authenticated channels; nothing in `KeyGenMachine` binds commitments to a simultaneous round). Via `ThresholdKeys::read`, any component feeding attacker-controlled serialized keys reaches it directly with crafted bytes. The PoK and share-verification checks all still pass, so the condition is silent — every participant completes `BlameMachine::complete` successfully.

### Recommendation
- Reject identity points in commitment deserialization: use the identity-rejecting `read_G` (as `frost::Curve::read_G` does) for `Commitments`/`EncryptionKeyMessage` in PedPoP, or explicitly check `commitments[j].is_identity()` in `verify_r1`.
- In `ThresholdKeys::new` (and hence `ThresholdKeys::read`), reject a zero `secret_share` and any identity `verification_shares`, and assert the computed `group_key` is non-identity — mirroring how the codebase already rejects zero elsewhere (`random_nonzero_F`, `scale` returning `None` on `0`, `Participant` being non-zero).
- Reject `share == 0` in `calculate_share`/`blame_internal`.

### Proof of Concept
Conceptual: with `t = n = 2`, participant 2 waits for participant 1's `EncryptionKeyMessage`, then broadcasts `Commitments { commitments: [−C10, ...], sig: PoK(−c10) }` where `C10` is participant 1's first commitment (its discrete log `−c10` is computable only if participant 2 wants PoK to pass with a known scalar — alternatively both slots identity suffices for the all-zero degenerate case when combined with `secret_share` sums). Simpler demonstrable case via `ThresholdKeys::read`: serialize `t=n=1`, `i=1`, `Interpolation::Lagrange`, `secret_share = 0`, `verification_shares = {1: identity}` — `read` returns `Ok` and `group_key() == identity`, yielding a functional "threshold key" with secret `0` that `view`/`sign` will use without error.

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L317-334)
```rust
      if msg.commitments.len() != self.params.t().into() {
        Err(PedPoPError::InvalidCommitments(l))?;
      }

      // Step 5: Validate each proof of knowledge
      // This is solely the prep step for the latter batch verification
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );

      commitments.insert(l, msg.commitments.drain(..).collect::<Vec<_>>());
    }

    batch.verify_vartime_with_vartime_blame().map_err(PedPoPError::InvalidCommitments)?;
```

**File:** crypto/dkg/pedpop/src/lib.rs (L479-491)
```rust
      let share =
        Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
          PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
        })?);
      share_bytes.zeroize();
      *self.secret += share.deref();

      blames.insert(l, blame);
      batch.queue(
        rng,
        BatchId::Share(l),
        share_verification_statements::<C>(self.params.i(), &self.commitments[&l], share),
      );
```

**File:** crypto/dkg/pedpop/src/lib.rs (L505-521)
```rust
    let mut stripes = Vec::with_capacity(usize::from(self.params.t()));
    for t in 0 .. usize::from(self.params.t()) {
      stripes.push(self.commitments.values().map(|commitments| commitments[t]).sum());
    }

    // Calculate each user's verification share
    let mut verification_shares = HashMap::new();
    for i in self.params.all_participant_indexes() {
      verification_shares.insert(
        i,
        if i == self.params.i() {
          C::generator() * self.secret.deref()
        } else {
          multiexp_vartime(&exponential::<C>(i, &stripes))
        },
      );
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

**File:** crypto/frost/src/curve/mod.rs (L125-131)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
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

**File:** crypto/dkg/src/lib.rs (L400-446)
```rust
  pub fn scale(mut self, scalar: C::F) -> Option<ThresholdKeys<C>> {
    if bool::from(scalar.is_zero()) {
      None?;
    }
    self.scalar *= scalar;
    self.offset *= scalar;
    Some(self)
  }

  /// Offset the keys by a given scalar to allow for various account and privacy schemes.
  ///
  /// This offset is ephemeral and will not be included when these keys are serialized. The
  /// offset is applied on top of any already-existing scalar/offset.
  #[must_use]
  pub fn offset(mut self, offset: C::F) -> ThresholdKeys<C> {
    self.offset += offset;
    self
  }

  /// Return the current scalar in-use for these keys.
  pub fn current_scalar(&self) -> C::F {
    self.scalar
  }

  /// Return the current offset in-use for these keys.
  pub fn current_offset(&self) -> C::F {
    self.offset
  }

  /// Return the parameters for these keys.
  pub fn params(&self) -> ThresholdParams {
    self.core.params
  }

  /// Return the original group key, without any tweaks applied.
  pub fn original_group_key(&self) -> C::G {
    self.core.group_key
  }

  /// Return the interpolation method for these keys.
  pub fn interpolation(&self) -> &Interpolation<C::F> {
    &self.core.interpolation
  }

  /// Return the group key, with the expected linear combination taken.
  pub fn group_key(&self) -> C::G {
    (self.core.group_key * self.scalar) + (C::generator() * self.offset)
```

**File:** crypto/dkg/src/lib.rs (L618-631)
```rust
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
