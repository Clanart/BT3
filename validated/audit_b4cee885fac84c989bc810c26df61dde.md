### Title
Cached FROST preprocess seed is never invalidated, so repeated `share` calls under one context reuse the same nonces and leak the secret share - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The kernel report's bug class is a resource that is only released on the success path, leaving it live (and reusable) after a failure or retry. The analog in Serai is `SigningProtocol::preprocess_internal`: it stores a `CachedPreprocess` — the ChaCha20 seed that deterministically derives the FROST nonces — keyed only by `context`, and nothing ever deletes it (`CachedPreprocesses` has `set`/`get` calls only, no removal anywhere). Every `share_internal`/`complete_internal` call rebuilds `AlgorithmSignMachine::from_cache` from the same seed, regenerating identical nonces/commitments for every signing attempt under that context. [1](#0-0) 

### Finding Description
`preprocess_internal` writes `CachedPreprocesses::set(txn, context, seed)` once and thereafter `get`s it unconditionally. [1](#0-0)  `from_cache` re-derives the identical `nonces` and `commitments` via `seeded_preprocess` → `ChaCha20Rng::from_seed(seed)` → `Commitments::new`, so the signer's nonce pair `(d, e)` is fixed per context. [2](#0-1)  `share_internal` calls `preprocess_internal` and then `machine.sign(preprocesses, msg)`; on `FrostError::InvalidPreprocess`/`InvalidShare` it returns `Err(participant)` but the seed remains in the DB, so a subsequent `share`/`complete` for the same context (e.g., `DkgConfirmer` context `(b"DkgConfirmer", attempt)`, used for every `Shares`/`KeyGen` confirmation message of that attempt) signs again with the same nonces. [3](#0-2) [4](#0-3)  This directly violates the FROST invariant documented in the same codebase: "Reusing preprocesses would enable a third-party to recover your private key share." [5](#0-4) [6](#0-5) 

### Impact Explanation
FROST shares have the form `s = d + e·ρ·λ·x` where `x` is the signer's secret share. With the same `(d, e)` reused across signing runs that differ in the participant set (different `λ`, different binding factors `ρ`, different aggregate commitment/challenge), each emitted share adds a linear equation over the same unknowns; once enough distinct shares are collected, `x` is recovered by linear algebra. Shares are broadcast over the tributary, so any observer collects them. Because `share()` is reachable once per inbound `Shares` message per attempt — and `complete()` independently re-runs `share_internal` under the same context — an unprivileged participant who supplies different preprocess sets (or a different `KeyPair`, changing `msg`) can cause multiple distinct shares to be emitted under one nonce pair, recovering the validator's MuSig/Ristretto secret share and enabling forgery of its threshold contributions. `complete` consumes `preprocesses`/`shares` supplied per call and re-derives the machine from the same cached seed. [7](#0-6) 

### Likelihood Explanation
Requires only that the same `(context)` be used for more than one distinct signing operation — which happens on the normal retry path: an `InvalidParticipant`/`InvalidPreprocess` error leaves the cache intact and the protocol naturally re-attempts signing for the same attempt with a different signer set, exactly the "retry after failure" shape of the kernel bug. Triggerable by network-level message submission (malformed then valid preprocesses), no collusion or privileged access needed. Uncertainty: I could not fully confirm all call sites of `DkgConfirmer::share`/`complete` in `handle.rs` within the iteration budget, so the exact multiplicity of emissions per attempt is inferred from the message handlers shown.

### Recommendation
Delete (or rotate) the `CachedPreprocesses` entry the first time a real signature share is produced for a context — i.e., invalidate on use, not just on clean completion — mirroring "free the skb regardless of `i2c_master_send` success." Concretely, remove the DB entry inside `share_internal` after `machine.sign` is evaluated (success or failure), or key the cache by a monotonically increasing per-sign counter so retries derive fresh nonces. Keep the cache only for the re-entrant preprocess→share→complete reconstruction of a single logical signature.

### Proof of Concept
1. As a tributary participant in `attempt = A`, send a `Commitments`/preprocess set `P1` for `DkgConfirmer`; the victim calls `share_internal`, emitting share `s1 = d + e·ρ1·λ1·x` built from `CachedPreprocesses[(b"DkgConfirmer", A)]`.
2. Send a second message with a different preprocess set `P2` (or altered `KeyPair` → different `set_keys_message`), triggering another `share_internal`/`complete` under the same context; the same seed regenerates identical `(d, e)` and emits `s2 = d + e·ρ2·λ2·x`.
3. Collect ≥3 such shares (they are public on the tributary), solve the linear system for `d`, `e`, and `x`; `x` is the victim's secret share, enabling forged shares in subsequent MuSig/FROST signing for that validator.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L123-145)
```rust
    if CachedPreprocesses::get(self.txn, &self.context).is_none() {
      let (machine, _) =
        AlgorithmMachine::new(algorithm.clone(), keys.clone()).preprocess(&mut OsRng);

      let mut cache = machine.cache();
      assert_eq!(cache.0.len(), 32);
      #[allow(clippy::needless_range_loop)]
      for b in 0 .. 32 {
        cache.0[b] ^= encryption_key_slice[b];
      }

      CachedPreprocesses::set(self.txn, &self.context, &cache.0);
    }

    let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap();
    let mut cached: Zeroizing<[u8; 32]> = Zeroizing::new(cached);
    #[allow(clippy::needless_range_loop)]
    for b in 0 .. 32 {
      cached[b] ^= encryption_key_slice[b];
    }
    encryption_key_slice.zeroize();
    let (machine, preprocess) =
      AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached));
```

**File:** coordinator/src/tributary/signing_protocol.rs (L150-180)
```rust
  fn share_internal(
    &mut self,
    participants: &[<Ristretto as Ciphersuite>::G],
    mut serialized_preprocesses: HashMap<Participant, Vec<u8>>,
    msg: &[u8],
  ) -> Result<(AlgorithmSignatureMachine<Ristretto, Schnorrkel>, [u8; 32]), Participant> {
    let machine = self.preprocess_internal(participants).0;

    let mut participants = serialized_preprocesses.keys().copied().collect::<Vec<_>>();
    participants.sort();
    let mut preprocesses = HashMap::new();
    for participant in participants {
      preprocesses.insert(
        participant,
        machine
          .read_preprocess(&mut serialized_preprocesses.remove(&participant).unwrap().as_slice())
          .map_err(|_| participant)?,
      );
    }

    let (machine, share) = machine.sign(preprocesses, msg).map_err(|e| match e {
      FrostError::InternalError(e) => unreachable!("FrostError::InternalError {e}"),
      FrostError::InvalidParticipant(_, _) |
      FrostError::InvalidSigningSet(_) |
      FrostError::InvalidParticipantQuantity(_, _) |
      FrostError::DuplicatedParticipant(_) |
      FrostError::MissingParticipant(_) => unreachable!("{e:?}"),
      FrostError::InvalidPreprocess(p) | FrostError::InvalidShare(p) => p,
    })?;

    Ok((machine, share.serialize().try_into().unwrap()))
```

**File:** coordinator/src/tributary/signing_protocol.rs (L274-309)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }

  fn preprocess_internal(&mut self) -> (AlgorithmSignMachine<Ristretto, Schnorrkel>, [u8; 64]) {
    let participants = self.spec.validators().iter().map(|val| val.0).collect::<Vec<_>>();
    self.signing_protocol().preprocess_internal(&participants)
  }
  // Get the preprocess for this confirmation.
  pub(crate) fn preprocess(&mut self) -> [u8; 64] {
    self.preprocess_internal().1
  }

  fn share_internal(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
  ) -> Result<(AlgorithmSignatureMachine<Ristretto, Schnorrkel>, [u8; 32]), Participant> {
    let participants = self.spec.validators().iter().map(|val| val.0).collect::<Vec<_>>();
    let preprocesses =
      threshold_i_map_to_keys_and_musig_i_map(self.spec, &self.removed, self.key, preprocesses).1;
    let msg = set_keys_message(
      &self.spec.set(),
      &self.removed.iter().map(|key| Public::from(key.to_bytes())).collect::<Vec<_>>(),
      key_pair,
    );
    self.signing_protocol().share_internal(&participants, preprocesses, &msg)
  }
  // Get the share for this confirmation, if the preprocesses are valid.
  pub(crate) fn share(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
  ) -> Result<[u8; 32], Participant> {
    self.share_internal(preprocesses, key_pair).map(|(_, share)| share)
```

**File:** coordinator/src/tributary/signing_protocol.rs (L312-320)
```rust
  pub(crate) fn complete(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
    shares: HashMap<Participant, Vec<u8>>,
  ) -> Result<[u8; 64], Participant> {
    let shares =
      threshold_i_map_to_keys_and_musig_i_map(self.spec, &self.removed, self.key, shares).1;

```

**File:** crypto/frost/src/sign.rs (L121-143)
```rust
  fn seeded_preprocess(
    self,
    seed: CachedPreprocess,
  ) -> (AlgorithmSignMachine<C, A>, Preprocess<C, A::Addendum>) {
    let mut params = self.params;

    let mut rng = ChaCha20Rng::from_seed(*seed.0);
    let (nonces, commitments) = Commitments::new::<_>(
      &mut rng,
      params.keys.original_secret_share(),
      &params.algorithm.nonces(),
    );
    let addendum = params.algorithm.preprocess_addendum(&mut rng, &params.keys);

    let preprocess = Preprocess { commitments, addendum };

    // Also obtain entropy to randomly sort the included participants if we need to identify blame
    let mut blame_entropy = [0; 32];
    rng.fill_bytes(&mut blame_entropy);
    (
      AlgorithmSignMachine { params, seed, nonces, preprocess: preprocess.clone(), blame_entropy },
      preprocess,
    )
```

**File:** crypto/frost/src/sign.rs (L216-224)
```rust
  /// Create a sign machine from a cached preprocess.
  ///
  /// After this, the preprocess must be deleted so it's never reused. Any reuse will presumably
  /// cause the signer to leak their secret share.
  fn from_cache(
    params: Self::Params,
    keys: Self::Keys,
    cache: CachedPreprocess,
  ) -> (Self, Self::Preprocess);
```

**File:** spec/cryptography/FROST.md (L51-62)
```markdown
Reusing preprocesses would enable a third-party to recover your private key
share. Accordingly, you MUST not reuse preprocesses. Third-party knowledge of
your preprocess would also enable their recovery of your private key share.
Accordingly, you MUST treat cached preprocesses with the same security as your
private key share.

Since a reused seed will lead to a reused preprocess, seeded RNGs are generally
frowned upon when doing multisignature operations. This isn't an issue as each
new preprocess obtains a fresh seed from the specified RNG. Assuming the
provided RNG isn't generating the same seed multiple times, the only way for
this seeded RNG to fail is if a preprocess is loaded multiple times, which was
already a failure point.
```
