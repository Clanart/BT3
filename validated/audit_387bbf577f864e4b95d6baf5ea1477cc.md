### Title
Consumed FROST cached preprocess seed is never cleared, allowing deterministic nonce reuse across distinct signed messages - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The ath11k bug is a lifecycle/stale-state defect: teardown destroys per-list state but leaves the `->initialized` flag set, so a later call operates on dead data. `SigningProtocol::preprocess_internal` has the same shape in the opposite direction: it persists a one-time-use FROST preprocess seed in `CachedPreprocesses` keyed only by `context`, and never deletes or rotates it after the seed has been consumed by `sign()`. The stale cache entry is the analog of the stale `initialized` flag — every subsequent call rebuilds the identical machine from the identical seed. [1](#0-0) 

### Finding Description
`preprocess_internal` checks `CachedPreprocesses::get(...)`; if absent it generates a seeded preprocess, XOR-encrypts the 32-byte seed, and stores it. It then always calls `AlgorithmSignMachine::from_cache(..., CachedPreprocess(cached))`, which routes into `seeded_preprocess` → `ChaCha20Rng::from_seed(*seed.0)` → `Commitments::new(...)`. The seed fully determines the nonces `(d, e)` and their commitments. [2](#0-1) 

The seed is never removed from the DB. `grep` confirms `CachedPreprocesses` has only `get`/`set` call sites — no deletion. Meanwhile `DkgConfirmer::share` and `DkgConfirmer::complete` both call `share_internal`, which calls `preprocess_internal` fresh each time:

- `share(preprocesses, key_pair)` → `share_internal` → `machine.sign(preprocesses, set_keys_message(set, removed, key_pair))`
- `complete(preprocesses, key_pair, shares)` → `share_internal` again → second `sign` with the same seed [3](#0-2) 

So the same deterministic nonce pair is used for two independent `sign()` invocations under a single `(b"DkgConfirmer", attempt)` context. The message embeds `key_pair`, which is a parameter supplied at each call site; if `share` and `complete` are ever invoked with differing `key_pair` values (e.g., retried confirmation over a regenerated/competing DKG result), or if `share_internal` is re-entered with a different preprocess map (different `included` set ⇒ different binding factor `ρ` and different aggregated commitment `R`), the reused nonces produce signature shares over different linear equations.

The FROST spec doc itself states the consequence: "Reusing preprocesses would enable a third-party to recover your private key share." [4](#0-3) 

`SignMachine::from_cache` likewise documents "the preprocess must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share" — yet `preprocess_internal` retains and re-serves the seed unconditionally. [5](#0-4) 

### Impact Explanation
The signed keys are MuSig `ThresholdKeys` over Ristretto used to confirm validator-set keys on Substrate (`set_keys_message`). A signature share has the form `z = d + e·ρ·λ + s·c` where `d, e` are the secret nonces, `ρ` is the binding factor, `λ` the Lagrange coefficient, `s` the secret share, `c` the challenge. Two shares produced with the same `(d, e)` but differing `(ρ, c, msg)` give an observer two equations; anyone who sees both shares (they are broadcast over the coordinator gossip and `share` values are serialized and sent to peers) can solve for `d, e` and then the secret share `s` — i.e., recovery of this validator's MuSig/FROST secret share for the confirming key. The encryption of the cached seed (XOR with a key derived from the private key) protects it at rest but does nothing for reuse.

### Likelihood Explanation
Triggering requires `share_internal` to execute more than once for the same `(b"DkgConfirmer", attempt)` context with any variation in `key_pair` or in the peer preprocess set. `complete` unconditionally re-derives the machine via `share_internal` (line 322), so any share→complete sequence already reuses the seed; whether the second `sign` leaks depends on the message/set differing. I could not fully trace `handle.rs` callers to confirm `key_pair` can differ between invocations within one attempt — that is the uncertain part. However, the peer preprocess map bytes are public inputs (serialized preprocesses supplied by other validators), and a re-attempt or retried confirmation path that supplies a different map changes `ρ`/`R` even with identical `key_pair`. The stash of a consumed one-time seed is structurally identical to the ath11k stale-flag defect and contradicts the code's own MUST-not-reuse contract.

### Recommendation
Delete the `CachedPreprocesses` entry immediately after `from_cache` consumes it (or after `sign` succeeds), e.g., a `CachedPreprocesses::del(txn, &context)` in `preprocess_internal` after seed recovery, so any second call generates a fresh seed. Alternatively, include a monotonically increasing nonce/round counter in the DB key so re-entry cannot replay the same seed. Audit `processor`/`batch_signer` preprocess caching paths for the same pattern.

### Proof of Concept
Conceptual (state-machine level):

```rust
// Same DkgConfirmer, same attempt => same CachedPreprocesses key.
// First call: seed S stored, machine signs msg1 = set_keys_message(set, removed, kp1)
let share1 = confirmer.share(preprocesses_a, &key_pair_1).unwrap();

// Second call: CachedPreprocesses::get returns the SAME seed S =>
// identical (d, e). If key_pair_2 != key_pair_1 or preprocesses_b selects a
// different included set, msg/challenge differs:
let share2 = confirmer.share(preprocesses_b, &key_pair_2).unwrap();

// share_i = d + e*rho_i*lambda_i + s*c_i
// Two equations, known rho_i, lambda_i, c_i -> solve for d, e, then s.
```

The reuse is deterministic because `seeded_preprocess` derives nonces solely from `ChaCha20Rng::from_seed(seed)`, and `preprocess_internal` re-serves the stored seed on every invocation with no consumption/invalidation step — the stale-`initialized`-flag analog.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L296-324)
```rust
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
