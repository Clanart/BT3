### Title
Generator promotion silently drops scalar and offset tweaks - (File: crypto/dkg/promote/src/lib.rs)

### Summary
`GeneratorPromotion::complete` reconstructs the promoted `ThresholdKeys` from the original secret share and promoted verification shares, but discards the base key's current scalar and offset. [1](#0-0)  A caller promoting a tweaked key therefore receives keys representing the untweaked base secret rather than the same logical key under the new generator. [2](#0-1) 

### Finding Description
`ThresholdKeys` carries an ephemeral scalar and offset that define its effective group key as `(original_group_key * scalar) + (generator * offset)`. [3](#0-2)  During promotion, the proof is generated for `original_secret_share` against `original_group_key`, which is appropriate for proving the underlying share. [4](#0-3)  However, `complete` calls `ThresholdKeys::new`, whose constructor always initializes `scalar` to `ONE` and `offset` to `ZERO`, instead of reapplying `base.current_scalar()` and `base.current_offset()` to the promoted result. [1](#0-0) [2](#0-1) 

This is analogous to a per-account value not being migrated during an account transition: the promoted object is returned for the same participant parameters and interpolation, but its effective key material silently reverts to the untweaked floor state. [1](#0-0) 

### Impact Explanation
If a caller promotes Bitcoin-style tweaked threshold keys, the resulting promoted key no longer corresponds to the tweaked key that was supplied. Subsequent signatures made with the promoted `ThresholdKeys` are valid for `C2::generator() * original_secret`, not for `scalar * original_secret + offset`. This can produce signatures under a different public key than the caller intended or make funds associated with the expected promoted tweaked key unspendable by the returned promoted shares. [5](#0-4) 

### Likelihood Explanation
The issue is reachable whenever an application promotes `ThresholdKeys` after applying `scale` or `offset`; Bitcoin's `tweak_keys` creates exactly such an ephemeral offset and possible `-1` scalar. [6](#0-5)  The attacker does not need control over the local share: public `GeneratorProof` inputs are consumed by `complete`, and all correctly completed promotions deterministically lose the tweak state. [7](#0-6) 

### Recommendation
Preserve the complete linear key transformation during promotion by applying the base key's `current_scalar()` and `current_offset()` to the newly constructed `ThresholdKeys<C2>` before returning it. The DLEq can continue proving the original secret share, but the resulting object should represent the same logical tweaked scalar under the new generator. [8](#0-7) 

### Proof of Concept
```rust
use zeroize::Zeroizing;
use dkg::{Interpolation, Participant, ThresholdParams, ThresholdKeys};
use dkg_promote::GeneratorPromotion;
use rand_core::OsRng;
use std::collections::HashMap;

let params = ThresholdParams::new(1, 1, Participant::new(1).unwrap()).unwrap();
let share = Zeroizing::new(<C1 as Ciphersuite>::F::random(&mut OsRng));
let mut verification_shares = HashMap::new();
verification_shares.insert(
  Participant::new(1).unwrap(),
  C1::generator() * *share,
);

let base = ThresholdKeys::<C1>::new(
  params,
  Interpolation::Constant(vec![<C1 as Ciphersuite>::F::ONE]),
  share,
  verification_shares,
).unwrap();

let base = base
  .scale(<C1 as Ciphersuite>::F::from(3)).unwrap()
  .offset(<C1 as Ciphersuite>::F::from(5));

let (promotion, proof) =
  GeneratorPromotion::<C1, C2>::promote(&mut OsRng, base.clone());
let promoted = promotion.complete(&HashMap::new()).unwrap();

// Actual result: scalar = 1, offset = 0.
assert_eq!(promoted.current_scalar(), <C2 as Ciphersuite>::F::ONE);
assert_eq!(promoted.current_offset(), <C2 as Ciphersuite>::F::ZERO);

// Expected migrated result.
let expected =
  (C2::generator() * (base.current_scalar() * *base.original_secret_share())) +
  (C2::generator() * base.current_offset());
assert_ne!(promoted.group_key(), expected);
```

The assertion demonstrates that `complete` returns the unpromoted linear combination rather than preserving the base key's current scalar and offset. [1](#0-0)

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L105-114)
```rust
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
```

**File:** crypto/dkg/promote/src/lib.rs (L120-166)
```rust
  pub fn complete(
    self,
    proofs: &HashMap<Participant, GeneratorProof<C1>>,
  ) -> Result<ThresholdKeys<C2>, PromotionError> {
    let params = self.base.params();
    if proofs.len() != (usize::from(params.n()) - 1) {
      Err(PromotionError::IncorrectAmountOfParticipants {
        t: params.n(),
        n: params.n(),
        amount: proofs.len() + 1,
      })?;
    }
    for i in proofs.keys().copied() {
      if u16::from(i) > params.n() {
        Err(PromotionError::InvalidParticipant { n: params.n(), participant: i })?;
      }
    }

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
    )
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

**File:** crypto/dkg/src/lib.rs (L393-417)
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
  }
```

**File:** crypto/dkg/src/lib.rs (L444-447)
```rust
  /// Return the group key, with the expected linear combination taken.
  pub fn group_key(&self) -> C::G {
    (self.core.group_key * self.scalar) + (C::generator() * self.offset)
  }
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

**File:** networks/bitcoin/src/wallet/mod.rs (L46-74)
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
```
