### Title
Consumed FROST preprocess seed is never invalidated, enabling deterministic nonce reuse and private key share recovery - ([File: coordinator/src/tributary/signing_protocol.rs])

### Summary
Analogous to the Carapace report — where a consumed entitlement (the withdrawal request) is left intact after capital is unlocked, letting it be spent again — `SigningProtocol::preprocess_internal` leaves the cached FROST preprocess seed in `CachedPreprocesses` after it has already been consumed to produce a signature share. Every subsequent signing action under the same context deterministically regenerates the identical nonces from that seed, so the same FROST nonce is used across multiple `sign` invocations — the exact failure the `CachedPreprocess` type documents as enabling third-party recovery of the private key share.

### Finding Description
`preprocess_internal` generates a preprocess machine seeded by a random 32-byte seed, XOR-encrypts it, and persists it under `CachedPreprocesses` keyed only by `context` (e.g., `(b"DkgConfirmer", attempt)`): [1](#0-0) . The entry is written once (`is_none()` guard) and is **never deleted or rotated** — `grep` shows `CachedPreprocesses` has only `get`/`set` uses in this file and no removal anywhere.

`from_cache` rebuilds the machine via `seeded_preprocess`, which derives both nonces deterministically with `ChaCha20Rng::from_seed(*seed.0)`: [2](#0-1) . `share_internal` calls `preprocess_internal` each time it runs: [3](#0-2) . Critically, `DkgConfirmer::complete` invokes `share_internal` **again** to reconstruct the machine: [4](#0-3) . So a `share` call followed by a `complete` call produces *two* signature shares derived from the same nonce commitments. If the preprocess set (signer subset, hence binding factors `rho` and challenge `e`) or the signed message (`set_keys_message` output, via a differing `key_pair` argument) differs between the two calls, the two shares are `s1 = n + λ·x·e1` and `s2 = n + λ·x·e2` over the same nonce `n`; subtracting yields the secret share `x` directly. This is precisely the "parallel-session / share reuse" condition the docs warn about: [5](#0-4) .

The public-input reachability is the same shape as the original bug: bytes supplied by the caller (`preprocesses` map and `key_pair`, i.e., the message being signed) feed `read_preprocess`/`sign`, and the protocol emits a second valid share over stale nonce state that was supposed to have been invalidated after first use — mirroring a withdrawal request remaining live after capital unlock.

### Impact Explanation
Reuse of a FROST nonce across two distinct challenges leaks the signer's long-lived private key share. Since these are MuSig-style threshold keys (`musig(...)` producing `ThresholdKeys`, all shares share the group key's aggregate secret structure), recovery of a share reduces the effective threshold and can enable signature forgery for the validator set's group key — a key-share-recovery severity (High).

### Likelihood Explanation
The divergence requirement is modest: `share` and `complete` are separately callable entry points over externally supplied preprocess sets and `key_pair`s, and any difference in the signing set or message between the two invocations produces the nonce-reuse pair — no malicious threshold participant or broken-BFT assumption is needed, just two `share_internal` invocations under the same context with non-identical inputs, which the API permits unconditionally since the cache is never cleared. Even a crash/retry path that re-runs `share` with a different collected preprocess set suffices.

### Recommendation
Delete the `CachedPreprocesses` entry atomically when it is consumed in `share_internal` (get-and-remove under the same DB transaction), so a second signing attempt under the same context regenerates a fresh seed rather than reusing the consumed one — the direct analog of zeroing `withdrawlRequested` when capital is unlocked.

### Proof of Concept
1. `DkgConfirmer::share(preprocesses_A, key_pair)` → `share_internal` → `preprocess_internal` creates and caches seed `s`, emits share `s1` over nonces derived from `ChaCha20Rng(s)` for message `m_A` / set `A`. `CachedPreprocesses[("DkgConfirmer", attempt)]` still holds `s`.
2. `DkgConfirmer::complete(preprocesses_B, key_pair)` (with `B ≠ A` or a differing `key_pair`) → `share_internal` → `preprocess_internal` reloads `s` via `from_cache` → identical nonces → emits share `s2` over a different challenge `e2`.
3. Observer computes `x = (s1 − s2) / (λ·(e1 − e2))` (or the Schnorrkel equivalent over the nonce-binding combination), recovering the signer's secret share — direct consequence of `seeded_preprocess` being deterministic in the persisted seed [6](#0-5)  and the cache never being cleared [7](#0-6) .

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

**File:** coordinator/src/tributary/signing_protocol.rs (L312-326)
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

**File:** spec/cryptography/FROST.md (L51-55)
```markdown
Reusing preprocesses would enable a third-party to recover your private key
share. Accordingly, you MUST not reuse preprocesses. Third-party knowledge of
your preprocess would also enable their recovery of your private key share.
Accordingly, you MUST treat cached preprocesses with the same security as your
private key share.
```
