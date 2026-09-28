### Title
FROST cached-preprocess reuse across distinct sign calls leaks the validator's secret key share - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The tributary coordinator persists a single FROST `CachedPreprocess` per `context` (e.g. `(b"DkgConfirmer", attempt)`) and rebuilds the sign machine from it on *every* call to `share_internal`, without ever marking the seed consumed. Because `AlgorithmSignMachine` derives its nonces deterministically from that cached seed, any two signing sessions that run under the same context reuse the same nonces while producing shares over different binding factors/messages — a logical "use-after-free" of a one-time nonce that enables recovery of the signer's private key share.

### Finding Description
`SigningProtocol::preprocess_internal` stores the `cache()` seed under `CachedPreprocesses` keyed by `self.context`, then on every invocation loads it back and calls `AlgorithmSignMachine::from_cache` [1](#0-0) . Nothing deletes or rotates the entry after `sign` consumes it.

`share_internal` always calls `preprocess_internal` first, so the nonce seed is reloaded each time a share is produced [2](#0-1) . `from_cache` goes through `seeded_preprocess`, meaning the nonce pair `(d, e)` is a deterministic function of the seed — identical for every call under the same context [3](#0-2) .

The FROST library itself states the invariant being violated: a cached preprocess "MUST only be used once. Reuse of it enables recovery of your private key share" and "the preprocess must be deleted so it's never reused" [4](#0-3) .

Reachable misuse paths in `DkgConfirmer`:

1. `share()` signs `set_keys_message(set, removed, key_pair)` [5](#0-4) . Within one `attempt` context, `share` can be invoked for more than one `key_pair`/preprocess set (each DKG confirmation inlined into the same attempt shares the attempt counter, per the comment at line 269-271). Each call reuses the same `(d, e)` but signs a *different* message and different participant binding factors ρ.
2. `complete()` calls `share_internal` a second time to rebuild the machine [6](#0-5)  — safe only if the caller replays identical inputs, which external data does not guarantee, since the preprocess map is supplied per-call and re-parsed via `read_preprocess` each time.

A signature share in FROST is `s_i = d_i + (ρ_i · e_i) + λ_i · x_i · c` (plus offsets). Two shares using identical `(d_i, e_i)` but different binding factors `ρ_i`, `ρ'_i` or challenges `c`, `c'` give two linear equations; an unprivileged counterparty who supplies the differing preprocesses knows `ρ` and `c` and solves for `x_i` — the validator's MuSig secret share, which is the actual signing key for the tributary set.

### Impact Explanation
Recovery of the validator's `Zeroizing<Ristretto::F>` MuSig secret (`self.key`). Since `musig(musig_context(...), self.key.clone(), ...)` is the node's validator key [7](#0-6) , a leaked share lets the attacker forge that validator's participation in tributary threshold signatures — equivalent to full compromise of that validator for the session. High severity: secret key material disclosure triggered by attacker-controlled protocol inputs.

### Likelihood Explanation
An attacker only needs to cause `share`/`complete` to be reached twice under the same `(label, attempt)` context with different public preprocess sets or `key_pair` values — both are unprivileged, attacker-influenced inputs (preprocesses come from other validators' messages; multiple key confirmations share an attempt). No collusion, no node compromise, and no race condition is required; the deterministic-seed design guarantees reuse whenever a second sign executes. The required second invocation depends on coordinator call patterns (re-processing of DKG confirmation data, or multiple key pairs per attempt), which is plausible but depends on tributary driver behavior not fully verifiable here — hence Moderate-to-High likelihood rather than certain.

### Recommendation
- Burn the cached preprocess on first use: delete `CachedPreprocesses::get` entry (or store a `consumed` flag) before calling `sign`, and refuse to sign if the flag is set.
- Alternatively, key the cache by a value unique per signing session (e.g., include the serialized preprocess set / message hash in `context`), so a retry with different inputs derives a different seed, while `complete` replaying identical inputs reproduces the same machine deterministically.
- Add an assertion in `share_internal` that the same `(context, msg)` pair is never signed twice with different `preprocesses`.

### Proof of Concept
Conceptual, on `DkgConfirmer` with `context = (b"DkgConfirmer", attempt)`:

1. Validator V calls `share(preprocesses_A, key_pair_1)`. `preprocess_internal` stores seed `S` and emits share `s1 = d + ρ1·e + λ·x·c1` over `msg1 = set_keys_message(.., key_pair_1)`.
2. Trigger a second `share(preprocesses_B, key_pair_2)` in the same attempt (different preprocess set → different ρ; different key pair → different `msg` → different `c`). The same seed `S` is reloaded → same `d, e`, same broadcast commitments.
3. Solve `x = ((s1 − s2) − e·(ρ1 − ρ2)) / (λ·(c1 − c2))` — all terms known to the attacker except `e`, which is recoverable first from the identical nonce commitments combined with the preprocess DLEq, or directly since `d, e` are reused: subtracting shares where ρ and c are both known yields `x` uniquely.

Root cause: `CachedPreprocesses::get` is re-read and re-decrypted unconditionally at signing_protocol.rs:137-145 instead of being single-use as required by `SignMachine::from_cache` docs.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L117-121)
```rust
    let algorithm = Schnorrkel::new(b"substrate");
    let keys: ThresholdKeys<Ristretto> =
      musig(musig_context(self.spec.set().into()), self.key.clone(), participants)
        .expect("signing for a set we aren't in/validator present multiple times")
        .into();
```

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

**File:** coordinator/src/tributary/signing_protocol.rs (L150-170)
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

**File:** crypto/frost/src/sign.rs (L268-274)
```rust
  fn from_cache(
    algorithm: A,
    keys: ThresholdKeys<C>,
    cache: CachedPreprocess,
  ) -> (Self, Self::Preprocess) {
    AlgorithmMachine::new(algorithm, keys).seeded_preprocess(cache)
  }
```
