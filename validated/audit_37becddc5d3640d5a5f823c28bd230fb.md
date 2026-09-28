### Title
Cached FROST preprocess seed is never deleted after use, enabling deterministic nonce reuse and key-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`preprocess_internal` stores the `CachedPreprocess` seed in `CachedPreprocesses` the first time it runs for a context, and `share_internal` / `complete` regenerate the sign machine from that persistent seed via `AlgorithmSignMachine::from_cache`. The DB entry is never removed. Since `from_cache` deterministically re-derives the nonces via `ChaCha20Rng::from_seed`, every subsequent call for the same context produces identical nonces — two signature shares over the same nonces with different binding factors/challenges leak the signer's secret share. This is a direct analog of the leaked resource in `cleanup_dev()`: an acquired object (`CachedPreprocesses::set`, like `usb_get_dev`) that the consuming path fails to release (`usb_put_dev` / no `CachedPreprocesses` delete), violating the explicit contract in `crypto/frost/src/sign.rs` that the cache "must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share." [1](#0-0) [2](#0-1) 

### Finding Description
In `SigningProtocol::preprocess_internal`, when `CachedPreprocesses::get` is empty, a seed is generated, XOR-encrypted, and persisted with `CachedPreprocesses::set` (lines 123–134). The machine is then rebuilt each call with `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))` (lines 137–145). There is no `CachedPreprocesses::remove`/delete anywhere — the seed lives in the DB forever for context `(b"DkgConfirmer", attempt)` [3](#0-2) .

`seeded_preprocess` derives nonces purely from the seed: `ChaCha20Rng::from_seed(*seed.0)` → `Commitments::new` → identical `(d_i, D_i)` nonce pairs on every invocation [4](#0-3) .

`DkgConfirmer::share` and `DkgConfirmer::complete` both route through `share_internal`, which calls `preprocess_internal` again — each call deterministically reproduces the same nonces [5](#0-4) .

### Impact Explanation
A FROST share has the form `share = f(nonces, rho) + challenge * secret_share * lagrange`. If `share` (or `share` followed by `complete`, or a second `share` after an intervening fault/retry in `handle.rs`) is invoked twice under the same `(b"DkgConfirmer", attempt)` context but with different supplied `preprocesses` or `key_pair`, the emitted shares reuse identical nonces while `rho` (bound to the preprocesses transcript) and `challenge` (bound to the aggregate commitment and `set_keys_message`) differ [6](#0-5) [7](#0-6) . Subtracting the two shares cancels the nonce term, yielding `secret_share` directly — full recovery of the validator's MuSig/threshold key share for the tributary signing set, i.e., compromise of the DKG-confirmation signing key.

### Likelihood Explanation
Reachable by an unprivileged validator peer: the preprocess set is attacker-supplied bytes (`read_preprocess` input), and `share()` is `pub(crate)` and can be exercised more than once for the same attempt context (e.g., `share` then `complete` with a differing `key_pair`, or a repeated share request after an `InvalidParticipant` retry path in `handle.rs`). Any second signing pass under the same context deterministically reuses the nonces because the cached seed is never invalidated. Requires no collusion threshold, no leaked keys, and no integrator misuse — the leak is created solely by the missing deletion.

### Recommendation
Delete `CachedPreprocesses[context]` before (or atomically with) `AlgorithmSignMachine::from_cache` in `preprocess_internal`, so each persisted seed can produce a sign machine exactly once; subsequent calls for the same context should fail rather than silently regenerate identical nonces. Alternatively key the DB entry by a monotonic nonce-session counter that is consumed on use.

### Proof of Concept
```rust
// Same context => same seed => identical nonces.
// Coordinator path: DkgConfirmer for attempt A.
let mut confirmer = DkgConfirmer::new(&key, &spec, &mut txn, attempt).unwrap();
let _our_preprocess = confirmer.preprocess(); // seed persisted under (b"DkgConfirmer", attempt)

// Pass 1: attacker supplies preprocess set P1 and key_pair K1
let share1 = confirmer.share(preprocesses_p1, &key_pair_1).unwrap();

// Pass 2: attacker supplies a different preprocess set P2 (different rho/challenge),
// or calls complete() with a different key_pair — same cached seed re-derives
// the same nonces.
let share2 = confirmer.share(preprocesses_p2, &key_pair_2).unwrap(); // or complete(...)

// share_i = nonce_val + c_i * secret_share * lambda  (same nonce_val both times)
// => secret_share = (share1 - share2) / ((c1 - c2) * lambda)  — secret recovered.
```
The reuse is guaranteed because `CachedPreprocesses::get` always returns the same seed until explicitly removed, and no removal exists in `coordinator/src/tributary/signing_protocol.rs` [8](#0-7) .

### Citations

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

**File:** coordinator/src/tributary/signing_protocol.rs (L274-277)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L288-326)
```rust
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
```

**File:** crypto/frost/src/sign.rs (L121-144)
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
  }
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

**File:** crypto/frost/src/sign.rs (L361-371)
```rust
      // Re-format into the FROST-expected rho transcript
      let mut rho_transcript = A::Transcript::new(b"FROST_rho");
      rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes());
      rho_transcript.append_message(b"message", C::hash_msg(msg));
      rho_transcript.append_message(
        b"preprocesses",
        C::hash_commitments(self.params.algorithm.transcript().challenge(b"preprocesses").as_ref()),
      );

      // Generate the per-signer binding factors
      B.calculate_binding_factors(&rho_transcript);
```
