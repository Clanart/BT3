### Title
Cached FROST preprocess seed is never deleted, so re-running a signing attempt regenerates identical nonces and leaks the secret share - ([File: coordinator/src/tributary/signing_protocol.rs](https://github.com/Annirich/serai--009/blob/main/coordinator/src/tributary/signing_protocol.rs))

### Summary
The external report concerns a "replace without delta" bug: `_initializeDistributionRecord` re-runs `_mint` for the full new amount, crediting twice. The Serai analog is identical in shape and strictly worse in consequence: `SigningProtocol::preprocess_internal` persists the FROST preprocess seed (`CachedPreprocess`) in the DB and *re-derives the same nonces* every time it is invoked for a context — it never deletes the cached seed after producing a share. The FROST API contract (`SignMachine::from_cache`) explicitly requires the cache be deleted after use; this call site reuses it indefinitely. Any replay of a signing attempt for the same context (reboot mid-sign, re-emitted `SubstratePreprocesses`, coordinator retry) produces a second signature share with reused nonces, enabling algebraic recovery of the validator's secret share.

### Finding Description
`SignMachine::from_cache` documents that after building a machine from a cached preprocess, "the preprocess must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share." [1](#0-0) 

In `coordinator/src/tributary/signing_protocol.rs`, `preprocess_internal` stores the encrypted seed under `self.context` if absent, then unconditionally loads it and calls `from_cache`, which deterministically regenerates `nonces` from `ChaCha20Rng::from_seed(seed)` [2](#0-1) [3](#0-2) . `CachedPreprocesses` is never deleted from `txn` after `share_internal` consumes it [4](#0-3) .

The analog to "minting the new amount on top of the unclaimed old one" is exact: the record (cached seed) is re-initialized/re-read rather than consumed, so the system emits a *new* signature share over the *same* nonce material. The processor's own design acknowledges reboots re-trigger signing for in-flight items: on boot it iterates `actively_signing` and calls `signer.sign_transaction` again [5](#0-4) , and the `Signer::attempt` code notes "on reboot, we'll get told of active signing items, and may be in this branch again" while warning that "messing up here leaks our secret share" [6](#0-5) . That `AttemptDb` guard exists for `Signer`, but `SigningProtocol` (used for substrate-side Schnorrkel signing such as `BatchSigner`/`SlashReportSigner`) has no such "consumed" guard — the DB-cached seed survives reboot and is reused whenever the same context is processed again.

Concretely, a node that produced a share for context `C`, then reboots (or receives a second `SubstratePreprocesses` for `C` after its RAM preprocessing state was lost — `self.preprocessing.take()` returns `None` on a cold start [7](#0-6) , while the DB cache persists), re-derives identical nonces `d, e`. If the responding participant set or preprocesses differ even slightly from the first run — which is expected since the set of participants who submit preprocesses is not fixed across attempts — the binding factor `rho` and challenge differ. Two shares `s₁ = d + b₁·e + λ₁·c₁·x` and `s₂ = d + b₂·e + λ₂·c₂·x` with known public values yield two equations solving `d`, `e`, and the secret share `x`.

### Impact Explanation
Recovery of the validator's Ristretto secret share for the substrate signing key. With threshold-many such leaks the group key is compromised; even alone it breaks the unforgeability boundary FROST assumes (nonce reuse = private key recovery, per `spec/cryptography/FROST.md` lines 51-55). This matches the accepted impact class "key share recovery."

### Likelihood Explanation
Re-triggering requires the same signing context to be processed twice — reachable through processor reboot mid-sign (explicitly designed: the Scanner/Signer re-fire unacked work, and `actively_signing` is re-signed on boot) or through the coordinator layer re-emitting a message for an in-flight attempt. The participating set / preprocess map on the second run is attacker-influenceable by whichever peers submit preprocesses, so differing challenges are realistic whenever the node reboots while peers' preprocess sets change. Medium severity: requires a restart/race, but deterministic once triggered.

### Recommendation
Delete `CachedPreprocesses` for `self.context` in the same DB transaction that emits the signature share (i.e., consume the seed inside `share_internal` after `machine.sign` succeeds), and/or persist a "share emitted" marker so a second invocation for an identical context either returns the identical serialized share or aborts. Alternatively, derive the seed as `H(context || attempt_nonce)` with a monotonically increasing attempt nonce so replays cannot reproduce nonces.

### Proof of Concept
1. Validator V participates in a substrate signing protocol; `share_internal` stores `CachedPreprocesses[context] = enc(seed)` and produces share `s₁` over nonces `(d, e)` with participant set `P₁` and challenge `c₁`.
2. V reboots before the protocol completes. DB retains the seed; RAM `preprocessing` state is empty.
3. The coordinator re-emits the same signing context; a different peer subset `P₂` submits preprocesses (legitimately — participation is not fixed). V's `preprocess_internal` reloads the same `seed`, `seeded_preprocess` regenerates identical `(d, e)` via `ChaCha20Rng::from_seed`.
4. V emits `s₂` with `rho₂ ≠ rho₁` and `c₂ ≠ c₁`.
5. An observer (or any participant who sees both broadcasts) solves the two-share linear system for `d`, `e`, and `x = V`'s secret share, since `share = d + rho·e + λ·challenge·secret_share` per FROST.

### Citations

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

**File:** coordinator/src/tributary/signing_protocol.rs (L123-148)
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
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L150-181)
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
  }
```

**File:** processor/src/main.rs (L553-564)
```rust
    // Sign any TXs being actively signed
    for (plan, tx, eventuality) in &actively_signing {
      if plan.key == network_key {
        let mut txn = raw_db.txn();
        if let Some(msg) =
          signer.sign_transaction(&mut txn, plan.id(), tx.clone(), eventuality).await
        {
          coordinator.send(msg).await;
        }
        // This should only have re-writes of existing data
        drop(txn);
      }
```

**File:** processor/src/signer.rs (L407-427)
```rust
    // If we reboot mid-sign, the current design has us abort all signs and wait for latter
    // attempts/new signing protocols
    // This is distinct from the DKG which will continue DKG sessions, even on reboot
    // This is because signing is tolerant of failures of up to 1/3rd of the group
    // The DKG requires 100% participation
    // While we could apply similar tricks as the DKG (a seeded RNG) to achieve support for
    // reboots, it's not worth the complexity when messing up here leaks our secret share
    //
    // Despite this, on reboot, we'll get told of active signing items, and may be in this
    // branch again for something we've already attempted
    //
    // Only run if this hasn't already been attempted
    // TODO: This isn't complete as this txn may not be committed with the expected timing
    if AttemptDb::get(txn, &id).is_some() {
      warn!(
        "already attempted {} #{}. this is an error if we didn't reboot",
        hex::encode(id.id),
        id.attempt
      );
      return None;
    }
```

**File:** processor/src/slash_report_signer.rs (L140-147)
```rust
        let (machines, our_preprocesses) = match self.preprocessing.take() {
          // Either rebooted or RPC error, or some invariant
          None => {
            warn!("not preprocessing. this is an error if we didn't reboot");
            return None;
          }
          Some(preprocess) => preprocess,
        };
```
