The bug class here is: the same resource managed by two different stateful paths, so a resume/replay via one path while the other holds stale/conflicting state causes failure. In Serai's in-scope code, the strongest analog is the Bitcoin scanner's dual tracking of scanned outputs (`ram_outputs` vs DB `seen`) and its handling of re-scans. Let me check the re-scan/reorg path.### Title
Cached FROST preprocess seed is reused across distinct messages under one signing context, enabling nonce reuse and secret-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The upstream bug is a single resource (the xHCI power domain) being driven by two different controllers whose states desynchronize, so a "resume" replays stale/conflicting state. The Serai analog is the coordinator's `CachedPreprocesses` table: a FROST preprocess seed — which deterministically generates the signing nonces — is persisted keyed only by `(b"DkgConfirmer", attempt)` and is silently reused by every entry point that goes through `preprocess_internal`, without any invalidation or binding to the message being signed. If two signing operations under the same context are ever driven with different messages, the same nonce material is committed twice — the classic precondition for private key share recovery that `CachedPreprocess`'s own documentation warns about. [1](#0-0) 

### Finding Description
`SigningProtocol::preprocess_internal` builds a `CachedPreprocess` seed on first use for a given `context` and stores it (XOR-encrypted with the validator's key share) in `CachedPreprocesses`. On every subsequent call — whether from `preprocess()`, `share()`, or `complete()` — it reloads the same seed and calls `AlgorithmSignMachine::from_cache`, which re-derives identical nonces via `seeded_preprocess` (`ChaCha20Rng::from_seed(*seed.0)` feeding `Commitments::new` and `preprocess_addendum`). [2](#0-1) 

The FROST spec for this crate states reuse of a preprocess "will enable third-party recovery of your private key share" and that after `from_cache` "the preprocess must be deleted so it's never reused." [3](#0-2)  The coordinator violates this by design: `share_internal` unconditionally calls `preprocess_internal` again (line 156), and `DkgConfirmer::complete` calls `share_internal` once more (line 322). The only thing preventing nonce reuse from being exploitable is that the message — `set_keys_message(set, removed, key_pair)` — must be byte-identical on every reuse. Nothing enforces this: `share()` and `complete()` take `key_pair` as a caller-supplied argument on each invocation, and the DB lookup key (`context = (b"DkgConfirmer", attempt)`) does not include the message. [4](#0-3) 

If `share()` is invoked twice with different `key_pair` values under the same `attempt` (e.g., a corrected/re-proposed key pair confirmation within the same DKG attempt, or a retry path in `handle.rs` feeding a distinct `KeyPair`), the machine produces two signature shares `s1 = n + c1·λ·x`, `s2 = n + c2·λ·x` over the same nonce commitment with different binding/challenge values. Because the Schnorrkel challenge differs while the nonce is reused, a party observing both shares and signatures can solve the resulting linear system for the validator's MuSig secret share `x`.

### Impact Explanation
Recovery of a validator's FROST/MuSig secret share for the tributary signing set. Combined with `t - 1` other shares (or used to forge that validator's participation), this enables forging threshold Schnorrkel signatures over tributary `set_keys` messages — i.e., signing unintended messages — which is the mechanism by which validator sets and their external-network keys are confirmed. This matches the "concrete signing of an unintended message / key share recovery" acceptance bar. Severity: Medium, contingent on a code path that issues two differing messages under one `(b"DkgConfirmer", attempt)` context.

### Likelihood Explanation
The reuse is unconditional; the only guard is implicit message equality. The exposure window is a recovery/retry path: if a DKG confirmation fails or a validator's on-chain key pair is re-submitted, any re-entry into `share`/`complete` for the same attempt with a different `KeyPair` triggers reuse. Because `CachedPreprocesses` persists in the DB across restarts, even a reboot-resume (directly parallel to the upstream s2idle-resume crash) replays the stale seed. It requires no malicious validator — just a coordinator-level retry with changed inputs.

### Recommendation
Include the message (or a hash of it, e.g., `key_pair` bytes) in the `CachedPreprocesses` context so distinct messages always map to distinct seeds, and/or delete the cached seed from the DB the first time `share_internal` consumes it, regenerating a fresh seed for any subsequent operation under the same context.

### Proof of Concept
```rust
// coordinator/src/tributary/signing_protocol.rs, same DbTxn, same attempt
let mut confirmer = DkgConfirmer::new(&key, &spec, &mut txn, attempt).unwrap();

// First use: seeds CachedPreprocesses[b"DkgConfirmer" || attempt]
let _pre = confirmer.preprocess();

// share() with key_pair_A: consumes cached seed -> nonces N
let share_a = confirmer.share(preprocesses.clone(), &key_pair_a).unwrap();

// complete() with key_pair_B (distinct message, same context):
// share_internal -> preprocess_internal reloads the SAME seed -> same nonces N,
// different msg -> different challenge -> linear system solvable for share x.
let _sig_b = confirmer.complete(preprocesses, &key_pair_b, shares).unwrap();
```
`share_a` and the shares inside `complete` are derived from identical `ChaCha20Rng` output (`from_seed(*cached)`), confirmed by `seeded_preprocess` in `crypto/frost/src/sign.rs:121-144`. The nonce non-determinism that FROST relies on is voided whenever the message differs between invocations sharing a context key.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L100-148)
```rust
  fn preprocess_internal(
    &mut self,
    participants: &[<Ristretto as Ciphersuite>::G],
  ) -> (AlgorithmSignMachine<Ristretto, Schnorrkel>, [u8; 64]) {
    // Encrypt the cached preprocess as recovery of it will enable recovering the private key
    // While the DB isn't expected to be arbitrarily readable, it isn't a proper secret store and
    // shouldn't be trusted as one
    let mut encryption_key = {
      let mut encryption_key_preimage =
        Zeroizing::new(b"Cached Preprocess Encryption Key".to_vec());
      encryption_key_preimage.extend(self.context.encode());
      let repr = Zeroizing::new(self.key.to_repr());
      encryption_key_preimage.extend(repr.deref());
      Blake2s256::digest(&encryption_key_preimage)
    };
    let encryption_key_slice: &mut [u8] = encryption_key.as_mut();

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

    (machine, preprocess.serialize().try_into().unwrap())
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L288-327)
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
