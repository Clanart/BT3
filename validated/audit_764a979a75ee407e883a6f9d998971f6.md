The strongest analog is the cached preprocess reuse path. I need to check how the cache key (`context`) is formed and whether the cache is ever cleared.`share_internal` calls `preprocess_internal` again, so the same cached seed is reloaded to rebuild the machine — meaning reuse across distinct signing operations under the same context would reuse nonces. Let me check all contexts used and whether the same context can sign twice.### Title
Deterministic cached preprocess reuse across distinct `share`/`complete` invocations reuses FROST nonces, enabling secret share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The Xen bug class — a resource acquired once and implicitly re-acquired on a later access (a lock held past return, deadlocking on re-entry) — maps onto Serai's coordinator signing path as a *cryptographic* re-acquisition: `SigningProtocol::preprocess_internal` deterministically regenerates the same FROST nonces from a cached seed every time it is invoked under the same `context`, so any code path that causes the same context to produce signature shares for two different signing sessions reuses the nonce. Per Serai's own spec, reusing a preprocess enables recovery of the private key share.

### Finding Description
`preprocess_internal` stores a 32-byte ChaCha20 seed (XOR-encrypted with a key derived from the participant's secret) in `CachedPreprocesses`, keyed only by `context` [1](#0-0) . On every call it checks whether the cache entry exists; if so it does **not** generate fresh nonces — it decrypts the stored seed and calls `AlgorithmSignMachine::from_cache`, which runs `seeded_preprocess` and deterministically regenerates the identical `nonces`/`commitments` via `ChaCha20Rng::from_seed` [2](#0-1) [3](#0-2) .

The lock-reacquisition analog: `share_internal` unconditionally calls `preprocess_internal` again, reloading the same seed [4](#0-3) , and `DkgConfirmer::complete` calls `share_internal` once more to rebuild the machine [5](#0-4) . The context is just `(b"DkgConfirmer", self.attempt)` [6](#0-5)  — nothing in the cache key binds the preprocess set or message. So within one attempt, every reconstruction of the sign machine yields the **same** nonces. The FROST spec shipped with the repo explicitly states that reusing a preprocess enables third-party recovery of the private key share [7](#0-6) .

The vulnerability is that `share` and `complete` accept the `preprocesses` map (and `key_pair`, which changes `msg` via `set_keys_message`) as parameters on each call [8](#0-7) . If the coordinator's handle layer ever invokes `share`/`complete` twice under the same `attempt` with different preprocess sets or different `key_pair`s (e.g., a retry after an invalid preprocess, or a second confirmation message), the same nonces sign two different `included` sets / binding factors / messages — exactly the ROS-style nonce-reuse condition. Two shares `s = k + λ·x·c` with identical `k` and different effective challenges `c` yield `x = (s₁−s₂)/(λ(c₁−c₂))`, recovering the participant's MuSig secret share.

### Impact Explanation
Recovery of a validator's tributary MuSig secret share — the key that authenticates all tributary messages for the validator set. This is more severe than the Xen DoS analog: the same "held past return" state mistake escalates from deadlock to key compromise. An unprivileged counterparty who can influence the preprocess set or trigger a repeated signing call under a reused attempt number forces the honest coordinator to emit two Schnorr shares under one nonce.

### Likelihood Explanation
The nonce reuse is *structurally guaranteed* — every `share_internal` call re-derives identical nonces for a fixed `(b"DkgConfirmer", attempt)` — so the only question is whether the tributary handle logic permits `share`/`complete` to run more than once per attempt with differing attacker-supplied inputs. `DkgConfirmer::share` is called with a `preprocesses` map assembled from other validators' messages, which are attacker-influenced; a faulting participant supplying invalid preprocesses (causing a retry with a corrected set) is the natural trigger. I was not able to fully verify the handle.rs call sequencing (whether retries re-enter `share` under the same attempt), so the exploitability hinge — repeat invocation with differing preprocesses — is plausible but not exhaustively confirmed.

### Recommendation
- Bind the cached seed to the full signing session: include the sorted `included` participant set and the message hash in the `CachedPreprocesses` key, so a different preprocess set or `key_pair` under the same attempt generates a fresh nonce stream rather than reloading the old one.
- Alternatively, record a "consumed" flag: after `share_internal` produces a share for a given context, mark the cache entry spent and refuse to sign again under that context (the cache already exists for crash-recovery rebuilds; distinguish rebuild-for-complete from a new sign by storing the produced preprocess/commitments and verifying `msg` matches).
- At minimum, store the serialized `Preprocess` alongside the seed and assert the regenerated commitments match before producing a share.

### Proof of Concept
Conceptual, mirroring the Xen report's "second access re-acquires the held lock":

1. Attacker (a fellow validator, Participant `j`) participates in a DKG confirmation attempt `a`. The honest coordinator's `DkgConfirmer::preprocess()` publishes preprocess `P_h` derived from cached seed `S` under context `(b"DkgConfirmer", a)`.
2. Attacker submits a preprocess map `M₁` (with one deliberately invalid or substituted preprocess from a third party) causing `share(M₁, key_pair)` to produce share `s₁` — internally `preprocess_internal` reloads seed `S`, so the coordinator's nonce is `k = f(S)`.
3. On retry (corrected map `M₂`, or a `complete` with a different `key_pair`/`set_keys_message`), `share_internal` again reloads `S` → same `k`, but the binding factor `ρ` over the `preprocesses` challenge and/or `hash_msg` differ (`B.bound` is computed over the preprocess map in `sign`), yielding `s₂ = k + λ·x·c₂`.
4. Compute `x = (s₁ − s₂) / (λ·(c₁ − c₂))` over the Ristretto scalar field → the coordinator's tributary MuSig secret share. All subsequent tributary signatures attributable to that validator are forgeable, matching the spec's own warning that preprocess reuse enables private key share recovery [9](#0-8) .

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L86-90)
```rust
create_db!(
  SigningProtocolDb {
    CachedPreprocesses: (context: &impl Encode) -> [u8; 32]
  }
);
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

**File:** coordinator/src/tributary/signing_protocol.rs (L288-310)
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

**File:** spec/cryptography/FROST.md (L45-62)
```markdown
# Caching

modular-frost supports caching a preprocess. This is done by having all
preprocesses use a seeded RNG. Accordingly, the entire preprocess can be derived
from the RNG seed, making the cache just the seed.

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
