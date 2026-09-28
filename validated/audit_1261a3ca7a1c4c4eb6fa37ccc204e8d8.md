### Title
Generator promotion silently discards scalar/offset key tweaks - (File: crypto/dkg/promote/src/lib.rs)

### Summary
`GeneratorPromotion` promotes only the untweaked `ThresholdKeys` state, silently discarding any scalar or offset previously applied through `ThresholdKeys::scale` or `ThresholdKeys::offset`. The resulting promoted key therefore represents the original group key rather than the key the caller actually supplied, making funds or signatures associated with the tweaked group key unavailable or invalid. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`ThresholdKeys` maintains ephemeral `scalar` and `offset` values which are incorporated into the effective group key as `original_group_key * scalar + generator * offset`. [4](#0-3) [3](#0-2) 

`GeneratorPromotion::promote` generates the new-generator share and DLEq proof exclusively from `original_secret_share` and binds the proof to `original_group_key`. [5](#0-4) 

`GeneratorPromotion::complete` then constructs the promoted `ThresholdKeys` with `ThresholdKeys::new`, using the original interpolation and original secret share. [2](#0-1) 

`ThresholdKeys::new` initializes `scalar` to one and `offset` to zero, so neither value from the supplied base key is carried into the promoted key. [6](#0-5) 

Consequently, promoting a tweaked key produces a key for the untweaked group while still succeeding and returning a seemingly valid result. [7](#0-6) 

### Impact Explanation
If a threshold key is tweaked before generator promotion—for example by a Taproot-style offset or another account-derivation tweak—the promoted key no longer corresponds to the group key that received funds or that external verifiers expect. [8](#0-7) [2](#0-1) 

Signatures produced with the promoted `ThresholdKeys` are calculated against the promoted, untweaked group key rather than the intended tweaked key. [9](#0-8) [10](#0-9) 

This can make funds received under the tweaked group key unspendable by the promoted key set, or cause signatures to verify for an unintended group identity. [2](#0-1) [3](#0-2) 

### Likelihood Explanation
The issue is reachable through the public `scale`, `offset`, `promote`, and `complete` APIs and does not require malformed proofs or invalid participant data. [8](#0-7) [11](#0-10) 

Every participant can provide a perfectly valid `GeneratorProof`, and `complete` still returns a key with the tweak omitted because verification is deliberately performed against the original verification shares. [12](#0-11) [2](#0-1) 

The severity is Medium because exploitation depends on generator promotion being used after key tweaking, but the failure is silent and can affect custody of funds already associated with the tweaked key. [13](#0-12) [14](#0-13) 

### Recommendation
Preserve the base key’s effective linear transformation during promotion by applying `base.current_scalar()` and `base.current_offset()` to the `ThresholdKeys<C2>` returned by `complete`, or explicitly reject promotion of keys whose scalar is not one or whose offset is nonzero. [15](#0-14) [2](#0-1) 

Preservation should apply the scalar first and then the offset so the promoted key represents `scalar * promoted_original_key + C2::generator() * offset`. [8](#0-7) [3](#0-2) 

If promotion of tweaked keys is intentionally unsupported, `promote` should return an error instead of silently producing a different key. [16](#0-15) 

### Proof of Concept
```rust
use rand_core::OsRng;

// `keys` is a valid ThresholdKeys<C1> obtained from the DKG.
let scalar = C1::F::from(3);
let offset = C1::F::from(7);
let tweaked = keys.scale(scalar).unwrap().offset(offset);

let intended_group_key = tweaked.group_key();

let (machine, own_proof) =
  GeneratorPromotion::<C1, C2>::promote(&mut OsRng, tweaked);

// Collect every other participant's GeneratorProof into `proofs`.
let promoted = machine.complete(&proofs).unwrap();

// The returned key dropped scalar and offset.
assert_eq!(promoted.current_scalar(), C1::F::ONE);
assert_eq!(promoted.current_offset(), C1::F::ZERO);

// It represents the untweaked promoted group, not the group supplied to promote.
assert_ne!(promoted.group_key(), intended_group_key);
```

The assertions follow because `promote` uses only `original_secret_share` and `complete` calls `ThresholdKeys::new`, whose scalar and offset fields are initialized to one and zero respectively. [5](#0-4) [2](#0-1) [6](#0-5)

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L101-123)
```rust
  pub fn promote<R: RngCore + CryptoRng>(
    rng: &mut R,
    base: ThresholdKeys<C1>,
  ) -> (GeneratorPromotion<C1, C2>, GeneratorProof<C1>) {
    // Do a DLEqProof for the new generator
    let proof = GeneratorProof {
      share: C2::generator() * base.original_secret_share().deref(),
      proof: DLEqProof::prove(
        rng,
        &mut transcript(&base.original_group_key(), base.params().i()),
        &[C1::generator(), C2::generator()],
        base.original_secret_share(),
      ),
    };

    (GeneratorPromotion { base, proof, _c2: PhantomData::<C2> }, proof)
  }

  /// Complete promotion by taking in the proofs from all other participants.
  pub fn complete(
    self,
    proofs: &HashMap<Participant, GeneratorProof<C1>>,
  ) -> Result<ThresholdKeys<C2>, PromotionError> {
```

**File:** crypto/dkg/promote/src/lib.rs (L138-165)
```rust
    let mut verification_shares = HashMap::new();
    verification_shares.insert(params.i(), self.proof.share);
    for i in 1 ..= params.n() {
      let i = Participant::new(i).unwrap();
      if i == params.i() {
        continue;
      }

      let proof = proofs.get(&i).unwrap();
      proof
        .proof
        .verify(
          &mut transcript(&self.base.original_group_key(), i),
          &[C1::generator(), C2::generator()],
          &[self.base.original_verification_share(i), proof.share],
        )
        .map_err(|_| PromotionError::InvalidProof(i))?;
      verification_shares.insert(i, proof.share);
    }

    Ok(
      ThresholdKeys::new(
        params,
        self.base.interpolation().clone(),
        self.base.original_secret_share().clone(),
        verification_shares,
      )
      .unwrap(),
```

**File:** crypto/dkg/src/lib.rs (L297-300)
```rust
  // Scalar applied to these keys.
  scalar: C::F,
  // Offset applied to these keys.
  offset: C::F,
```

**File:** crypto/dkg/src/lib.rs (L380-390)
```rust
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

**File:** crypto/dkg/src/lib.rs (L393-416)
```rust
  /// Scale the keys by a given scalar to allow for various account and privacy schemes.
  ///
  /// This scalar is ephemeral and will not be included when these keys are serialized. The
  /// scalar is applied on top of any already-existing scalar/offset.
  ///
  /// Returns `None` if the scalar is equal to `0`.
  #[must_use]
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
```

**File:** crypto/dkg/src/lib.rs (L420-427)
```rust
  pub fn current_scalar(&self) -> C::F {
    self.scalar
  }

  /// Return the current offset in-use for these keys.
  pub fn current_offset(&self) -> C::F {
    self.offset
  }
```

**File:** crypto/dkg/src/lib.rs (L444-447)
```rust
  /// Return the group key, with the expected linear combination taken.
  pub fn group_key(&self) -> C::G {
    (self.core.group_key * self.scalar) + (C::generator() * self.offset)
  }
```

**File:** crypto/dkg/src/lib.rs (L523-532)
```rust
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
