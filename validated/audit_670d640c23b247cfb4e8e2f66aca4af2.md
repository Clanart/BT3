### Title
Cached FROST preprocess seed is never flushed, enabling nonce reuse across distinct signer sets/messages - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
Like CVE-2017-13693 (an operand cache that is never invalidated and leaks stale secret state), `SigningProtocol::preprocess_internal` writes a deterministic preprocess seed into `CachedPreprocesses` keyed only by `context` and never deletes or rotates it. Every subsequent call to `share_internal` (which re-runs `preprocess_internal`) rebuilds the *same* `AlgorithmSignMachine` with the *same* nonces. If two shares are produced under the same context with different inputs, the secret share is recoverable.

### Finding Description
In `preprocess_internal`, the seed is created once and stored; on later calls the cached seed is decrypted and passed to `AlgorithmSignMachine::from_cache`, which calls `seeded_preprocess`. `seeded_preprocess` derives nonces deterministically via `ChaCha20Rng::from_seed(*seed.0)`, so identical seeds yield identical `nonces` and identical `commitments` [1](#0-0) [2](#0-1) .

`share_internal` calls `preprocess_internal` unconditionally [3](#0-2) , and `DkgConfirmer::complete` calls `share_internal` a second time for the same `(b"DkgConfirmer", attempt)` context [4](#0-3) . There is no `CachedPreprocesses::delete`/overwrite after a successful sign, so the same nonce material persists for the lifetime of the context.

The FROST share is `s = nonce + lambda * challenge * secret_share`. Both the challenge `c` and the binding factor `rho` depend on the included signer set and the message (`hash_msg`, `preprocesses` challenge). The `preprocesses` map and `key_pair` fed to `share`/`complete` originate from tributary messages; two calls with (a) different included preprocess sets, or (b) a different `key_pair` (hence different `set_keys_message` output [5](#0-4) ), produce two shares over the same nonce with different challenges — a classic FROST/ROS-style nonce-reuse break. The FROST docs themselves acknowledge reuse of a cached preprocess "will enable third-party recovery of your private key share" [6](#0-5) .

### Impact Explanation
Recovery of the validator's Ristretto secret share under the validator-set MuSig key. Since shares within one signer set/interpolation can be combined, leaking the share under two challenges yields `secret_share = (s1 - s2) / (lambda1*c1 - lambda2*c2)`. For a DKG-confirmer key this compromises the validator's signing authority; analogous cached reuse in other `SigningProtocol` contexts compromises the key that context protects.

### Likelihood Explanation
Reachable by any party able to feed distinct `preprocesses`/`key_pair` inputs into `share` or `complete` for the same attempt — i.e., tributary message originators — without needing the DB or colluding validators. The trigger condition (a second sign under a reused context with differing parameters) arises whenever confirmation inputs aren't perfectly deduplicated, which `share`'s signature explicitly permits by taking attacker-influenced maps per call.

### Recommendation
- After a share is successfully produced, delete or rotate `CachedPreprocesses` for that context (`CachedPreprocesses::del(txn, &context)`), and on cache miss within an already-signed context, generate a fresh seed rather than reusing.
- Alternatively bind the cached entry to a hash of (preprocess set, msg) and refuse to sign if a subsequent request differs from the committed binding.

### Proof of Concept
1. As a tributary participant, call `DkgConfirmer::share(preprocesses_A, key_pair_1)` — internally `share_internal` → `preprocess_internal` decrypts cached seed `s`, derives nonce `d`/`e`, emits share `s1 = d + b·e + λ1·c1·x`.
2. Call `DkgConfirmer::complete(preprocesses_B, key_pair_2, shares)` with `preprocesses_B` differing in the included set (or `key_pair_2 ≠ key_pair_1`) — `share_internal` regenerates the *same* `d`/`e` from the untouched cache and computes `s2 = d + b·e + λ2·c2·x` internally.
3. Obtaining both shares (broadcast on the tributary channel), solve `x = (s1 - s2) / (λ1·c1 - λ2·c2)`, recovering the validator's secret share — mirroring CVE-2017-13693's "stale cached state disclosed because the cache was never flushed."

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

**File:** coordinator/src/tributary/signing_protocol.rs (L296-301)
```rust
    let msg = set_keys_message(
      &self.spec.set(),
      &self.removed.iter().map(|key| Public::from(key.to_bytes())).collect::<Vec<_>>(),
      key_pair,
    );
    self.signing_protocol().share_internal(&participants, preprocesses, &msg)
```

**File:** coordinator/src/tributary/signing_protocol.rs (L312-327)
```rust
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

**File:** crypto/frost/src/sign.rs (L83-92)
```rust
/// A cached preprocess.
///
/// A preprocess MUST only be used once. Reuse will enable third-party recovery of your private
/// key share. Additionally, this MUST be handled with the same security as your private key share,
/// as knowledge of it also enables recovery.
// Directly exposes the [u8; 32] member to void needing to route through std::io interfaces.
// Still uses Zeroizing internally so when users grab it, they have a higher likelihood of
// appreciating how to handle it and don't immediately start copying it just by grabbing it.
#[derive(Zeroize)]
pub struct CachedPreprocess(pub Zeroizing<[u8; 32]>);
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
