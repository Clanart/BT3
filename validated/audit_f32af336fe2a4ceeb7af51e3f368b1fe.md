### Title
Cached FROST preprocess seed is looked up by `context` alone while the MuSig keys are regenerated from the live participant set, enabling nonce reuse across distinct sessions - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` regenerates the `ThresholdKeys` on every call via `musig(musig_context(...), self.key.clone(), participants)`, where `participants` is the caller's current validator list. However, the preprocess seed that deterministically derives all nonces is cached/loaded under `CachedPreprocesses` keyed only by `self.context` (e.g. `(b"DkgConfirmer", attempt)`), not by the participant set or the resulting keys. This is the direct analog of CVE-2022-27778: curl writes to the no-clobber-renamed file (`foo.1`) but unlinks the original name (`foo`) — here the *regenerated identity* (the MuSig keys over the live participant set) is used for signing, while the *stale identifier* (context alone) decides which secret material is reused. [1](#0-0) 

### Finding Description
On every call, `preprocess_internal` builds fresh `keys` from the supplied `participants` slice [2](#0-1) , but reuses the cached 32-byte seed whenever `CachedPreprocesses::get(self.txn, &self.context)` returns a value [3](#0-2) . `AlgorithmSignMachine::from_cache` feeds that seed to `seeded_preprocess`, which seeds `ChaCha20Rng` and regenerates identical nonces via `Commitments::new` [4](#0-3) . The nonces are therefore a pure function of `(seed, secret_share, nonce count)` — the participant set and group key are not inputs.

The `DkgConfirmer` context is `(b"DkgConfirmer", self.attempt)` [5](#0-4) , while the participants passed in are `self.spec.validators()` for the preprocess and `threshold_i_map_to_keys_and_musig_i_map(..., &self.removed, ...)` for the share — i.e., the MuSig roster is filtered by `removed` only at share time [6](#0-5) . If the effective `removed` set / message (`set_keys_message` binds `set`, `removed`, `key_pair`) changes between two calls sharing the same `attempt` context — e.g., a retried confirmation with a different `key_pair` or a different removal set — the same nonces are committed under two different challenges `c1 ≠ c2`. Given two shares `s1 = d + ρe + λc1·x` and `s2 = d + ρe + λc2·x` (same preprocesses ⇒ same ρ, same nonces), `x = (s1 − s2) / (λ(c1 − c2))` recovers the signer's MuSig secret share. The code itself documents that reuse "will enable third-party recovery of your private key share" [7](#0-6) .

### Impact Explanation
Recovery of a validator's Ristretto MuSig secret key (`self.key`), which authenticates it in the tributary DKG-confirmation signing protocol. Knowledge of two broadcast signature shares — public protocol data — suffices for the algebraic recovery; no secret material needs to be read. This satisfies the "key share recovery" acceptance criterion.

### Likelihood Explanation
Medium-to-low. Exploitation requires the coordinator to produce two `share` outputs under the same `attempt` context while the bound inputs (`key_pair`, `removed`, or signer set) differ — reachable through retry/attempt paths and validator-set changes rather than purely attacker-controlled bytes. It does not require corrupting the DB or colluding signers; the deterministic-seed reuse under a context that ignores the regenerated keys is a code-level invariant violation analogous to the curl unlink. Whether a second `share` is actually emitted for the same attempt in production flow could not be fully confirmed within available iterations; if it never is, this reduces to a latent footgun rather than an exploitable path.

### Recommendation
Bind the cached-preprocess lookup to the full session identity: key `CachedPreprocesses` by `context` plus a hash of the participant list and the message/`removed` set, or derive the seed as `H(seed || participants || msg)`. Alternatively, regenerate a fresh seed whenever `preprocess_internal` is re-entered for signing (the cache exists so a rebooted node can resume, not so a live node can reuse nonces), and refuse to sign twice for the same context unless all bound inputs are identical.

### Proof of Concept
1. Attempt `a` begins; `preprocess` caches seed `S` under `(b"DkgConfirmer", a)` and emits commitments `D, E`.
2. `share` is invoked with `key_pair` `K1` (or before a removal is applied): machine is rebuilt via `from_cache(S)` → same nonces `d, e`; share `s1` is produced for `msg1 = set_keys_message(set, removed1, K1)`.
3. A retry calls `share` again under the same `attempt` with `key_pair` `K2 ≠ K1` (or `removed2 ≠ removed1`): same seed → same nonces, `msg2 ≠ msg1` → challenge `c2 ≠ c1`; share `s2` is emitted.
4. With identical preprocesses (same ρ) and known Lagrange coefficient λ, compute `x = (s1 − s2)·(λ(c1 − c2))⁻¹`, recovering the validator's MuSig secret key.

Uncertainty note: the primitive (seed reuse keyed by context while keys are regenerated from live inputs) is confirmed in code; the exact coordinator call path that issues two `share` invocations for one `attempt` was not fully traced, so real-world triggerability is stated conservatively.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L117-145)
```rust
    let algorithm = Schnorrkel::new(b"substrate");
    let keys: ThresholdKeys<Ristretto> =
      musig(musig_context(self.spec.set().into()), self.key.clone(), participants)
        .expect("signing for a set we aren't in/validator present multiple times")
        .into();

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

**File:** coordinator/src/tributary/signing_protocol.rs (L274-277)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L288-302)
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
