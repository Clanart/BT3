### Title
`GeneratorPromotion::complete` drops the base keys' scalar/offset, silently re-keying promoted shares to the un-tweaked (pre-retraction) key - (File: crypto/dkg/promote/src/lib.rs)

### Summary
`ThresholdKeys` support ephemeral `scalar`/`offset` tweaks used to restrict or re-derive a key's authority (e.g., `tweak_keys` in `networks/bitcoin/src/wallet/mod.rs` adds an unspendable Taproot script path and negates odd keys — effectively *retracting* the bare key's ability to authorize arbitrary spends). `GeneratorPromotion::promote`/`complete` promote shares to a new generator using only `original_secret_share()` and `original_group_key()`, and rebuild the result via `ThresholdKeys::new`, which hard-codes `scalar: C::F::ONE` and `offset: C::F::ZERO`. The tweak — the very mechanism that revokes/alters the key's privileges — is never propagated, mirroring CVE-2023-7090 where `ipa_hostname` was not propagated and clients retained privileges after retraction. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`ThresholdKeys::scale`/`offset` accumulate into `self.scalar`/`self.offset`, and `group_key()` computes `(core.group_key * scalar) + (G * offset)` [4](#0-3) . `GeneratorPromotion::promote` proves a DLEq for `original_secret_share()` against `original_group_key()` — deliberately ignoring the active linear combination [5](#0-4) . `complete` then constructs the promoted `ThresholdKeys<C2>` from `self.base.original_secret_share().clone()` and the base `interpolation`, with no application of `base.current_scalar()`/`base.current_offset()` [3](#0-2) . Because `ThresholdKeys::new` unconditionally sets `scalar = F::ONE, offset = F::ZERO` [6](#0-5) , the promoted key's `group_key()` is `C2::generator() * original_secret` — the un-tweaked identity — while every other holder of the same logical key (who applied e.g. `tweak_keys` or an HDKD offset) expects `C2::generator() * (scalar * original_secret + offset)`.

The bug is reachable via the untrusted `proofs: &HashMap<Participant, GeneratorProof<C1>>` argument to `complete` (populated via `GeneratorProof::read` in-scope deserialization), though it triggers independently of proof contents whenever promotion is run on tweaked keys.

### Impact Explanation
Privilege mismanagement / key-identity confusion analogous to the sudo flaw: after promotion, the threshold shares retain signing authority for the *pre-tweak* group key that the tweak was supposed to constrain (e.g., the unspendable-script-path / even-parity Taproot key produced by `tweak_keys` [7](#0-6) ). Conversely, any funds or authorizations addressed to the tweaked group key are unspendable by the promoted set, since `group_key()` and the per-signer `secret_share` (via `view()`, which re-derives `scalar * share` and adds `offset` to `included[0]` [8](#0-7) ) no longer correspond to it. Either outcome — signatures under the wrong group key, or reported-receivable funds that the multisig cannot spend — is an accepted impact.

### Likelihood Explanation
Medium. The flaw is deterministic and requires no adversarial skill beyond triggering a generator promotion on keys that carry a non-trivial scalar/offset — exactly what `tweak_keys` produces for every Bitcoin key in this codebase. Any protocol path that promotes Bitcoin (or otherwise offset/scaled) keys will hit it. It does require the promote flow to be exercised on tweaked keys, and the DLEq verification itself still passes (it correctly binds `original_group_key` per-participant [9](#0-8) ), so nothing fails loudly — the mismatch is silent until signatures/addresses are compared.

### Recommendation
Propagate the base keys' linear combination in `GeneratorPromotion::complete`: construct the result as `ThresholdKeys::new(...)?.scale(base.current_scalar()).map(|k| k.offset(base.current_offset()))` (note `scale` multiplies the existing offset, so apply scalar then offset consistently with the original order), or store the intended `group_key` and assert `promoted.group_key() == C2::generator() * (scalar * secret + offset)` before returning. Alternatively, document and enforce (via an error in `promote`) that only un-tweaked keys (`scalar == 1 && offset == 0`) may be promoted.

### Proof of Concept
```rust
// crypto/dkg/promote/src/tests.rs-style, Ristretto + AltGenerator<Ristretto>
let keys = dkg_dealer::key_gen::<_, Ristretto>(&mut OsRng, 2, 3);
let participant = Participant::new(1).unwrap();
let base = keys[&participant].clone().offset(<Ristretto as Ciphersuite>::F::random(&mut OsRng));
let tweaked_group = base.group_key(); // G * (s + offset)

let (promotion, proof) = GeneratorPromotion::<_, AltGenerator<Ristretto>>::promote(&mut OsRng, base.clone());
// ... gather other proofs, then:
let promoted = promotion.complete(&proofs_from_others).unwrap();

// Expected: AltGenerator::generator() * (s + offset)  (the tweaked identity)
// Actual:   AltGenerator::generator() * s            (tweak silently dropped)
assert_ne!(
  promoted.group_key(),
  AltGenerator::<Ristretto>::generator() * (*base.original_secret_share() + base.current_offset())
);
// i.e. promoted.group_key() == AltGenerator::generator() * *base.original_secret_share()
```
The test in `crypto/dkg/promote/src/tests.rs:50-113` passes only because it never applies `scale`/`offset` before promoting; adding a single `.offset(...)` to the generated keys exposes the dropped tweak.

### Citations

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

**File:** crypto/dkg/src/lib.rs (L400-447)
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
  }
```

**File:** crypto/dkg/src/lib.rs (L494-521)
```rust
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

**File:** crypto/dkg/promote/src/lib.rs (L105-116)
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

    (GeneratorPromotion { base, proof, _c2: PhantomData::<C2> }, proof)
```

**File:** crypto/dkg/promote/src/lib.rs (L148-154)
```rust
        .proof
        .verify(
          &mut transcript(&self.base.original_group_key(), i),
          &[C1::generator(), C2::generator()],
          &[self.base.original_verification_share(i), proof.share],
        )
        .map_err(|_| PromotionError::InvalidProof(i))?;
```

**File:** crypto/dkg/promote/src/lib.rs (L158-166)
```rust
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
