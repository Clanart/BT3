### Title
DKG confirmer reuses a cached FROST preprocess (deterministic nonces) across `share`/`complete` and across multiple key-pair confirmations, enabling secret-share recovery - ([File: coordinator/src/tributary/signing_protocol.rs](coordinator/src/tributary/signing_protocol.rs))

### Summary
`SigningProtocol::preprocess_internal` stores a 32-byte seed for FROST nonces in `CachedPreprocesses` keyed only by `context`, and never deletes it after the signature share is produced. Every subsequent call with the same context (`(b"DkgConfirmer", attempt)`) regenerates the *same* binomial nonces via `AlgorithmSignMachine::from_cache` → `seeded_preprocess` → `ChaCha20Rng::from_seed`. This is the direct analog of "a resource remains accessible after de-allocation": the preprocess seed is consumed at share-generation time but remains mapped in the DB, so a second signing run under the same context silently reuses it. FROST documentation in this repo explicitly states a cached preprocess "MUST only be used once. Reuse will enable third-party recovery of your private key share" [1](#0-0) .

### Finding Description
In `preprocess_internal`, the cache is created once per context and then read back on every invocation without ever being removed:

- Seed stored once: `if CachedPreprocesses::get(self.txn, &self.context).is_none() { ... CachedPreprocesses::set(...) }` then unconditionally `CachedPreprocesses::get(self.txn, &self.context).unwrap()` and `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))` [2](#0-1) .
- `from_cache` re-derives the identical nonces from the seed (`ChaCha20Rng::from_seed(*seed.0)` → `Commitments::new`) [3](#0-2) .

`share_internal` calls `preprocess_internal` and then `machine.sign(preprocesses, msg)` where `preprocesses` and `msg` come from attacker-influenced inputs [4](#0-3) .

Two reachable paths reuse the same seed under the same `(b"DkgConfirmer", attempt)` context:

1. `DkgConfirmer::share` signs `set_keys_message(set, removed, key_pair)`; `DkgConfirmer::complete` calls `share_internal` **again** with caller-supplied `preprocesses` before completing [5](#0-4) . If the included participant set (or any commitment) in `complete` differs from the set used in `share`, the per-participant binding factors ρ differ, so the second signature share is `s_i·λ + (d + ρ·e)·c'` — the same raw nonce `(d, e)` under a different binding factor and/or different message hash. Two equations in two unknowns (`s_i`, `d + ρ·e`) recover `s_i`, the MuSig private key share.
2. `generated_key_pair` in `handle.rs` is invoked per key pair within the same attempt [6](#0-5) . Multiple `KeyPair` confirmations in one DKG attempt produce shares over *different messages* (`set_keys_message` commits to `key_pair`) with identical nonces — the classic Schnorr nonce-reuse equation `s_i = (z_1 − z_2)/(c_1 − c_2)`, directly recovering the validator's private key share.

### Impact Explanation
The affected key is the validator's MuSig secret share inside `ThresholdKeys<Ristretto>` used to sign substrate `set_keys` messages and, transitively, all protocol signing. Recovery of one share reduces the threshold security; combined with reuse across the `m` parallel machines pattern elsewhere, multiple shares may leak. Concretely this yields forged DKG confirmations / validator-set key attestations and any signature Schnorrkel/`AlgorithmSignMachine` produces — i.e., signing of unintended messages, exactly the accepted impact class "key share recovery".

### Likelihood Explanation
Path (1) requires a participant to submit different preprocess bytes for `share` vs `complete`, which tributary transactions carry and which reach `read_preprocess` — within the allowed "messages they cause to be signed" reachability, though it does require being a validator (borderline under the malicious-validator rejection rule). Path (2) needs only the coordinator protocol to confirm more than one key pair in a single attempt — normal operation, no malicious party required beyond public message flow, making it the stronger analog.

### Recommendation
Delete the `CachedPreprocesses` entry the first time it is consumed for `sign` (e.g., `txn.del` after `CachedPreprocesses::get` in `share_internal`, or scope the context to a single signature instance), so a second `share_internal` under the same context generates a fresh seed instead of reusing the freed-but-still-mapped one.

### Proof of Concept
1. In one DKG `attempt`, `generated_key_pair` is invoked for `key_pair_A`, producing share `z_A` over `msg_A = set_keys_message(.., A)` with nonce seed `S` (cached under `(b"DkgConfirmer", attempt)`).
2. `generated_key_pair` (or `complete`) is invoked again in the same `attempt` for `key_pair_B`, reloading `S` → same nonces `(d, e)` → share `z_B` over `msg_B` with challenge `c_B ≠ c_A`.
3. Recover the MuSig secret share: `s_i = (z_A − z_B) / (c_A − c_B)` over the scalar field, since the nonce term `(d + ρ_i·e)` is identical and cancels. [7](#0-6)

### Citations

**File:** crypto/frost/src/sign.rs (L121-141)
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
```

**File:** crypto/frost/src/sign.rs (L209-224)
```rust
  /// Cache this preprocess for usage later.
  ///
  /// This cached preprocess MUST only be used once. Reuse of it enables recovery of your private
  /// key share. Third-party recovery of a cached preprocess also enables recovery of your private
  /// key share, so this MUST be treated with the same security as your private key share.
  fn cache(self) -> CachedPreprocess;

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

**File:** coordinator/src/tributary/signing_protocol.rs (L123-147)
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

    (machine, preprocess.serialize().try_into().unwrap())
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

**File:** coordinator/src/tributary/signing_protocol.rs (L274-301)
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
```

**File:** coordinator/src/tributary/signing_protocol.rs (L304-327)
```rust
  pub(crate) fn share(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
  ) -> Result<[u8; 32], Participant> {
    self.share_internal(preprocesses, key_pair).map(|(_, share)| share)
  }

  pub(crate) fn complete(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
    shares: HashMap<Participant, Vec<u8>>,
  ) -> Result<[u8; 64], Participant> {
    let shares =
      threshold_i_map_to_keys_and_musig_i_map(self.spec, &self.removed, self.key, shares).1;

    let machine = self
      .share_internal(preprocesses, key_pair)
      .expect("trying to complete a machine which failed to preprocess")
      .0;

    DkgConfirmerSigningProtocol::<'_, T>::complete_internal(machine, shares)
  }
```

**File:** coordinator/src/tributary/handle.rs (L47-60)
```rust
pub fn generated_key_pair<D: Db>(
  txn: &mut D::Transaction<'_>,
  key: &Zeroizing<<Ristretto as Ciphersuite>::F>,
  spec: &TributarySpec,
  key_pair: &KeyPair,
  attempt: u32,
) -> Result<[u8; 32], Participant> {
  DkgKeyPair::set(txn, spec.genesis(), attempt, key_pair);
  KeyToDkgAttempt::set(txn, key_pair.0 .0, &attempt);
  let preprocesses = ConfirmationNonces::get(txn, spec.genesis(), attempt).unwrap();
  DkgConfirmer::new(key, spec, txn, attempt)
    .expect("claiming to have generated a key pair for an unrecognized attempt")
    .share(preprocesses, key_pair)
}
```
