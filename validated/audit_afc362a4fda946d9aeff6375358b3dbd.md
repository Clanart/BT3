### Title
Missing release of cached FROST preprocess enables deterministic nonce reuse across sign calls in the same context — (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The analogous defect to CVE-2020-12656 (missing `domain_release` on the cleanup/unload path) is a missing "release" of the cached FROST preprocess. `SigningProtocol::preprocess_internal` stores a `CachedPreprocess` seed in the database keyed by `context` (e.g., `(b"DkgConfirmer", attempt)`) but never deletes or rotates it. Because both `share` and `complete` re-derive the identical nonces from that seed via `share_internal` → `preprocess_internal` → `seeded_preprocess`, two `sign()` executions under the same context consume the same nonces. If the two executions bind different messages, this is classic Schnorr nonce reuse and the validator's MuSig/FROST secret share is recoverable.

### Finding Description [1](#0-0)  `preprocess_internal` writes `CachedPreprocesses::set` only `if get(...).is_none()`, and there is no deletion anywhere in the codebase (`grep` shows `CachedPreprocesses::` only in this file). The seed is XOR-decrypted and passed to `AlgorithmSignMachine::from_cache`. [2](#0-1)  `seeded_preprocess` seeds `ChaCha20Rng::from_seed(*seed.0)` and derives both `nonces` and `commitments` purely deterministically from it. Identical seed → identical `(d, e)` nonce pair. [3](#0-2)  `share_internal` calls `self.preprocess_internal(participants).0`, building a fresh `AlgorithmSignMachine` with the *same* nonces, then calls `machine.sign(preprocesses, msg)`, which consumes the nonce pair into a signature share. [4](#0-3)  `DkgConfirmer::share` and `DkgConfirmer::complete` both funnel through `share_internal` with the *same* `context` (`(b"DkgConfirmer", self.attempt)`). The signed message is `set_keys_message(&spec.set(), &removed, key_pair)` — a function of the supplied `key_pair`. `complete` calls `share_internal` a second time (line 322), producing a second `sign()` under identical nonces. If `key_pair` differs between the two calls (e.g., a first `share` for one KeyPair candidate and `complete`/`share` for another, or any retry path that changes the confirmed key pair), the validator emits two Schnorr shares `(d + be) − λ·c·s` and `(d + be) − λ·c′·s` with the same nonce over different challenges, yielding `s = Δshare / (λ·Δc)`.

The exact analog of the CVE: a resource (nonce seed / kernel mechanism domain) is acquired and never released (`set`-without-`del` vs. missing `domain_release`), so a later operation silently reuses the stale resource instead of obtaining a fresh one.

### Impact Explanation
Recovery of the validator's MuSig secret share for the validator-set key (the Schnorrkel `KeyPair` signing `set_keys_message`). Nonce reuse in Schnorr/FROST directly exposes the linear relation between share, challenge, and Lagrange coefficient; two shares suffice to solve for the secret share. Unlike the CVE's mere memory leak, here the stale resource is cryptographically toxic — the accepted impact class "key share recovery" applies.

### Likelihood Explanation
The reuse window is real: `share` is called when preprocesses arrive, and `complete` is called later when shares arrive, each independently rebuilding the machine from the same never-deleted seed (signing_protocol.rs:304-327). Any path where the node emits a share for context `attempt`, then later signs again for the same `attempt` with a different `key_pair` or different effective `msg` (e.g., competing DKG-confirmer results for the same attempt, or a `share` followed by `complete` whose reconstructed preprocess map alters `included`/binding factors) produces shares under a reused nonce. The adversary only needs to feed preprocesses/shares — public protocol inputs — to drive both calls; no privileged access is required. The caveat is that identical `msg` and identical `included` yield identical shares (no leak), so exploitation requires divergence in the bound transcript, which the protocol's retry/multiple-candidate structure makes plausible.

### Recommendation
Delete the cached preprocess after use: call a `CachedPreprocesses::del(txn, &context)` (or overwrite with a freshly generated seed) at the first point the nonces are consumed — i.e., inside `share_internal` after `machine.sign` succeeds — so any subsequent `sign()` under the same context generates fresh nonces rather than replaying the stale seed. Alternatively, make `preprocess_internal` consume-once: generate-and-store on first use, then remove the DB entry when returning the machine for `sign`.

### Proof of Concept
Conceptual trace:
1. `DkgConfirmer::share(preprocesses, key_pair_A)` → `share_internal` → `preprocess_internal` derives seed `S` from `CachedPreprocesses["DkgConfirmer", attempt]` → `sign(preprocesses, msg_A)` emits share `σ_A = n − λ_A·c_A·s` where `n = d + b·e` is fixed by `S`.
2. `DkgConfirmer::complete(preprocesses', key_pair_B, shares)` → `share_internal` again re-derives the identical `n` from `S` (seed never deleted) → `sign` emits `σ_B = n − λ_B·c_B·s` over `msg_B = set_keys_message(..., key_pair_B)`.
3. `s = (σ_B − σ_A) / (λ_A·c_A − λ_B·c_B) mod l` — the validator's Ristretto secret share is recovered, and since this is a MuSig (n-of-n) member key, it contributes directly toward forging validator-set signatures.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L150-180)
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
      FrostError::InternalError(e) => unreachable!("FrostError::InternalError {e}"),
      FrostError::InvalidParticipant(_, _) |
      FrostError::InvalidSigningSet(_) |
      FrostError::InvalidParticipantQuantity(_, _) |
      FrostError::DuplicatedParticipant(_) |
      FrostError::MissingParticipant(_) => unreachable!("{e:?}"),
      FrostError::InvalidPreprocess(p) | FrostError::InvalidShare(p) => p,
    })?;

    Ok((machine, share.serialize().try_into().unwrap()))
```

**File:** coordinator/src/tributary/signing_protocol.rs (L304-327)
```rust
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

**File:** crypto/frost/src/sign.rs (L127-133)
```rust
    let mut rng = ChaCha20Rng::from_seed(*seed.0);
    let (nonces, commitments) = Commitments::new::<_>(
      &mut rng,
      params.keys.original_secret_share(),
      &params.algorithm.nonces(),
    );
    let addendum = params.algorithm.preprocess_addendum(&mut rng, &params.keys);
```
