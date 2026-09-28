### Title
Cached FROST preprocess seed keyed only by `(tag, attempt)` is reused across distinct messages, enabling nonce reuse and secret share recovery — (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` caches the FROST preprocess RNG seed under `CachedPreprocesses::get(self.txn, &self.context)`, where `context` for `DkgConfirmer` is only `(b"DkgConfirmer", self.attempt)`. The cached seed deterministically regenerates the same nonces via `AlgorithmMachine::seeded_preprocess` (`ChaCha20Rng::from_seed(*seed.0)`). Because the context does not bind the message being signed (`key_pair` in `set_keys_message`), any second `share`/`complete` call within the same attempt signs a *different* message with the *same* nonces — classic Schnorr nonce reuse, allowing an unprivileged participant who observes both shares to recover this validator's secret key share. This mirrors the report's bug class: a piece of per-context state (the payee / the preprocess seed) is set once and not reset when the surrounding context (owner / message) changes.

### Finding Description
In `coordinator/src/tributary/signing_protocol.rs`:

- `preprocess_internal` writes the encrypted seed once per context and never invalidates it: `if CachedPreprocesses::get(self.txn, &self.context).is_none() { ... CachedPreprocesses::set(...) }` (lines 123–135), then always reconstructs the sign machine `from_cache` (lines 137–145).
- `DkgConfirmer::signing_protocol` sets `context = (b"DkgConfirmer", self.attempt)` (line 275). It contains no reference to the message, the `key_pair`, or the signing set.
- `DkgConfirmer::share`/`complete` (lines 288–327) build `msg = set_keys_message(&self.spec.set(), &removed, key_pair)` — a value supplied at call time. Two invocations with different `key_pair`s (e.g., a retried/mutated confirmation, or confirming both generated keys) reuse the same cached nonces.
- In `crypto/frost/src/sign.rs`, `seeded_preprocess` (lines 121–144) derives `nonces` purely from `ChaCha20Rng::from_seed(*seed.0)` plus `original_secret_share`; identical seed + identical share ⇒ identical nonces and identical commitments regardless of `msg`.
- The docs in `crypto/frost/src/sign.rs` (lines 211–219) and `spec/cryptography/FROST.md` (lines 45–62) explicitly warn: "a reused seed will lead to a reused preprocess … Reusing preprocesses would enable a third-party to recover your private key share."

The analog to "payee is not reset on transfer": the cached preprocess is attached to the *attempt*, and is not reset when the *message* (the thing being authorized, analogous to the new owner) changes. The protocol effectively re-signs under stale nonce state.

### Impact Explanation
An observer (any fellow validator, or anyone who sees two broadcast signature shares) who obtains two shares/signature transcripts produced under the same `(b"DkgConfirmer", attempt)` context but different `key_pair` messages can solve for the signer's nonce and recover their MuSig/Ristretto secret share. Since these shares sign `set_keys_message` — the confirmation that installs validator set keys on Serai — recovery of threshold-many shares (or of a key reused across such confirmations) compromises the validator-set authentication key. This is key share recovery, the highest-impact accepted class.

### Likelihood Explanation
Requires the same attempt to sign more than one distinct `key_pair` message, which can happen if a DKG confirmation is retried with a corrected key pair, if `share` and `complete` are invoked on divergent inputs, or if multiple key pairs are confirmed per attempt (the Serai DKG generates a *pair* of keys per session — external-network key plus Ristretto key — which are each candidates for `set_keys_message`). No malicious validator, leaked key, or collusion is needed beyond the normal multi-call flow; the persistent DB cache guarantees identical nonces across calls.

### Recommendation
Bind the cached preprocess to the full signing context — e.g., include a hash of `set_keys_message(...)` (or the `key_pair`) in `self.context`, so each distinct message gets a fresh seed — or delete `CachedPreprocesses::get/set` for `context` after the first successful `share` (reset-on-use, the direct analog of `delete s.lienMeta[id].payee` on transfer). Additionally, persist and compare the preprocess bytes actually published so a machine never emits a different share under reused commitments.

### Proof of Concept
```text
1. Validator V runs DkgConfirmer for attempt A.
   context = (b"DkgConfirmer", A); no cache exists → seed S generated, stored.
   preprocess P = f(S, share_V) is broadcast (commitments C).
2. V calls share(preprocesses, key_pair_1):
   msg_1 = set_keys_message(set, removed, key_pair_1)
   from_cache regenerates nonces (d, e) from S → share s_1 = d + e·ρ_1 + λ·share_V·c_1
   where c_1 = Hram(C_aggregate, R_1, key, msg_1).
3. V calls share(preprocesses, key_pair_2) (different key_pair, same attempt):
   cached seed S reused → same (d, e), same published commitments C.
   msg_2 = set_keys_message(set, removed, key_pair_2)
   share s_2 = d + e·ρ_2 + λ·share_V·c_2, with c_2 = Hram(..., msg_2).
   (ρ binding factors also differ since the transcript binds hash_msg / msg.)
4. An observer with s_1, s_2 and the public challenge values solves the
   2×2 linear system for (d + e·ρ) terms and then for share_V:
   s_1 - s_2 = e·(ρ_1 - ρ_2) + λ·share_V·(c_1 - c_2) → recover share_V.
```

Supporting code:
- Cache keyed by context only, never reset: `coordinator/src/tributary/signing_protocol.rs:123-145` [1](#0-0) 
- Context excludes message/key_pair: `coordinator/src/tributary/signing_protocol.rs:274-276` [2](#0-1) 
- Deterministic nonce derivation from seed: `crypto/frost/src/sign.rs:121-144` [3](#0-2) 
- Documented consequence (share recovery on reuse): `crypto/frost/src/sign.rs:211-219`, `spec/cryptography/FROST.md:51-62` [4](#0-3)

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

**File:** crypto/frost/src/sign.rs (L211-219)
```rust
  /// This cached preprocess MUST only be used once. Reuse of it enables recovery of your private
  /// key share. Third-party recovery of a cached preprocess also enables recovery of your private
  /// key share, so this MUST be treated with the same security as your private key share.
  fn cache(self) -> CachedPreprocess;

  /// Create a sign machine from a cached preprocess.
  ///
  /// After this, the preprocess must be deleted so it's never reused. Any reuse will presumably
  /// cause the signer to leak their secret share.
```
