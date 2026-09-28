### Title
Cached FROST preprocess seed is never invalidated, enabling deterministic nonce reuse across distinct signing calls (MuSig key share recovery) - ([File: coordinator/src/tributary/signing_protocol.rs](coordinator/src/tributary/signing_protocol.rs))

### Summary
`SigningProtocol::preprocess_internal` stores the ChaCha20 seed for a FROST preprocess in `CachedPreprocesses` keyed only by `context` and reuses it on every subsequent call for the same context. Both `share()` and `complete()` (and `preprocess()`) re-derive identical nonces from this stale cached seed. The kernel bug class — a buffer written once and then executed without invalidation/flush — maps directly here: the nonce buffer is built once and replayed for every later signing invocation under the same context, with no one-shot guard.

### Finding Description
In `preprocess_internal`, if `CachedPreprocesses::get(txn, context)` is already set, no new entropy is drawn; the stored seed is decrypted and passed to `AlgorithmSignMachine::from_cache` → `seeded_preprocess`, which derives nonces deterministically via `ChaCha20Rng::from_seed(seed)` [1](#0-0) [2](#0-1) . The cache entry is never deleted after use.

`share_internal` calls `preprocess_internal` fresh every time [3](#0-2) . For `DkgConfirmer`, `context = (b"DkgConfirmer", attempt)`, so all calls within one attempt share the seed [4](#0-3) . The signed message is `set_keys_message(set, removed, key_pair)`, which varies with the caller-supplied `key_pair` and `preprocesses` arguments [5](#0-4) . Consequently, a second `share()` call under the same attempt — e.g., triggered by a distinct preprocess set or distinct `key_pair` arriving via the tributary — produces a second signature share using identical `(d, e)` nonces over a different binding factor ρ and/or challenge c.

Two shares s₁ = d + e·ρ₁·λ + λ·x·c₁ and s₂ = d + e·ρ₂·λ + λ·x·c₂ let the observer recover e = (s₁−s₂) / (λ·(ρ₁−ρ₂)), then d, and once the nonces are known the private MuSig key share x is recoverable whenever c differs (or directly if ρ matches but c differs). This is precisely the "reused preprocesses enable recovery of your private key share" hazard documented in `crypto/frost/src/sign.rs` [6](#0-5)  and `spec/cryptography/FROST.md` [7](#0-6)  — the code itself creates the reuse by design.

### Impact Explanation
Recovery of a validator's MuSig/Ristretto private key share compromises its threshold-signing capability for the set-confirmation protocol; combined with threshold-many compromised shares, arbitrary `set_keys` messages can be forged. Analogous to the CVE's "stale buffer executed as if fresh," the stale seed causes the signer to emit cryptographically valid shares over attacker-influenced messages while reusing secret nonces — a Critical/High key-exposure primitive.

### Likelihood Explanation
Reachability requires the coordinator to invoke `share()`/`complete()` more than once for the same `(b"DkgConfirmer", attempt)` context with differing `preprocesses`/`key_pair` inputs — data supplied by other validators' published preprocesses and key-pair claims (public inputs an unprivileged counterparty feeds via `read_preprocess`/share messages). A malicious validator submitting conflicting preprocess sets for one attempt, or a retry of `complete` after `share` under a changed key_pair, triggers a second share with reused nonces. Whether the tributary handler deduplicates multiple share requests per attempt could not be fully confirmed within the available search iterations; if it does not, the attack is directly reachable. Even absent deliberate triggering, any retry path reuses nonces by construction.

### Recommendation
Key the cached preprocess by the full signing tuple (context plus hash of the preprocess set / message), or invalidate (`txn.del`) the `CachedPreprocesses` entry after the first `sign()` consumption and refuse to sign under a context whose cache was consumed. Prefer deriving nonces bound to the message/commitment set rather than a fixed seed.

### Proof of Concept
Conceptual: within one DKG attempt, validator A (malicious) causes honest validator V's coordinator to call `DkgConfirmer::share(preprocesses_S1, key_pair_K)` and later `share(preprocesses_S2, key_pair_K')` (or `complete` after a mutated preprocess map). Both calls hit `preprocess_internal` → `CachedPreprocesses` hit → identical seed → identical nonces d,e. Collect V's two shares; solve for e via the differing binding factors ρ₁≠ρ₂ (or directly for x if the challenge differs), recovering V's MuSig key share.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L150-156)
```rust
  fn share_internal(
    &mut self,
    participants: &[<Ristretto as Ciphersuite>::G],
    mut serialized_preprocesses: HashMap<Participant, Vec<u8>>,
    msg: &[u8],
  ) -> Result<(AlgorithmSignatureMachine<Ristretto, Schnorrkel>, [u8; 32]), Participant> {
    let machine = self.preprocess_internal(participants).0;
```

**File:** coordinator/src/tributary/signing_protocol.rs (L274-277)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L288-301)
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

**File:** crypto/frost/src/sign.rs (L209-219)
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
