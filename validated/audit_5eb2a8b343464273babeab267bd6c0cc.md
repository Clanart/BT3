### Title
Deterministic CachedPreprocess reuse signs multiple messages with identical FROST nonces, enabling key-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` caches a single 32-byte seed in `CachedPreprocesses` keyed only by `self.context` (for `DkgConfirmer`, the tuple `(b"DkgConfirmer", attempt)`). Every subsequent call — `share()` and `complete()` both route through `share_internal`, which itself calls `preprocess_internal` — loads the same seed and reconstructs the same `AlgorithmSignMachine` via `from_cache`/`seeded_preprocess`, which drives `ChaCha20Rng::from_seed(*seed.0)` to regenerate byte-identical nonces and commitments [1](#0-0) [2](#0-1) . If two executions under the same `context` sign different messages, the FROST nonces are reused across distinct challenges, allowing recovery of the signer's secret share — the documented catastrophic consequence of preprocess reuse [3](#0-2) .

### Finding Description
The analog to the CVE's "shared-state race causing corruption" is a reuse-of-invocation race on a persistent, deterministic nonce seed:

- `preprocess_internal` checks `CachedPreprocesses::get(self.txn, &self.context)`; if a seed exists it is decrypted and passed to `AlgorithmSignMachine::from_cache` unconditionally, with no per-message or per-invocation nonce state and no deletion after use [4](#0-3) .
- `from_cache` calls `seeded_preprocess`, which seeds `ChaCha20Rng` from the stored 32 bytes and derives `nonces`/`commitments` deterministically [5](#0-4) .
- The cache key contains only `(b"DkgConfirmer", self.attempt)` — not the `key_pair`, not the participant set, not the message [6](#0-5) .
- `DkgConfirmer::share` and `DkgConfirmer::complete` each independently call `share_internal`, and the signed message is `set_keys_message(set, removed, key_pair)`, which varies with `key_pair` [7](#0-6) .

Therefore, two invocations under the same attempt that differ in `key_pair` (or that interleave `share`/`complete` calls with different `key_pair` arguments — e.g., a `share` issued, then a `complete` issued against a re-emitted KeyGen event carrying a distinct key pair, or a reboot/retry path where the attempt counter does not advance) produce two Schnorr signature shares `s₁ = d + ρ·b·e + λ·x·c₁` and `s₂ = d + ρ·b·e + λ·x·c₂` over the identical nonce commitment. The nonce terms cancel: `(s₁ − s₂)/(c₁ − c₂) = λ·x`, and since the Lagrange coefficient `λ` for participant `i` is publicly computable, the secret share `x` is recovered [8](#0-7) .

Even the single-message path is fragile: `share()` then `complete()` re-derive the machine twice from the same seed, so any caller-level retry or concurrent `share`/`complete` (a racing double-submission, matching the CVE's race flavor) re-executes `sign` with the same nonces against whatever `key_pair` is in flight.

### Impact Explanation
Recovery of a coordinator's FROST/MuSig secret share over `Ristretto`/`Schnorrkel` for the tributary validator set. Combined with `t-1` other shares (or used to forge that signer's contribution), this enables signature forgery for `set_keys_message` confirmations and breaks the threshold assumption of the DKG-confirmer signing protocol. This satisfies the "key share recovery" acceptance criterion.

### Likelihood Explanation
Triggering requires two `share_internal` executions under one `(b"DkgConfirmer", attempt)` context with differing `key_pair`/message bytes. This is reachable when: (a) a `share` and later `complete` are invoked with a `key_pair` that changed between calls (re-emitted `KeyGen` events), or (b) retries/reboots re-enter `share` after the `CachedPreprocesses` row was persisted but the message differs. Because the DB row is never consumed/rotated on `sign`, the reuse is deterministic rather than probabilistic — the same nonces are guaranteed. An unprivileged party cannot directly choose `key_pair`, but the failure is triggered by ordinary protocol races/retries rather than requiring a malicious validator, so likelihood is moderate; impact is high.

### Recommendation
- Bind the cache key to the full signing instance: `CachedPreprocesses` should be keyed by `(context, msg_digest)` or the seed should be derived as `H(seed || msg || participants)` so distinct messages cannot reuse nonces.
- Consume the cached seed on first `sign` (delete the row or mark it spent within the same DB transaction as `share_internal`), and have `complete` reuse the already-built `AlgorithmSignatureMachine` rather than re-deriving it via `share_internal`.
- Alternatively, drop the persistent cache for `DkgConfirmer` and generate a fresh `OsRng` preprocess per attempt since these are infrequent operations.

### Proof of Concept
Conceptual, from the code paths above:

1. Attempt `a` begins; `preprocess_internal` stores seed `S` under `("DkgConfirmer", a)` and emits preprocess `P` with commitments `D, E` derived from `ChaCha20Rng::from_seed(S)` [1](#0-0) [9](#0-8) .
2. `share(preprocesses, key_pair_A)` → `share_internal` → `preprocess_internal` reloads `S`, rebuilds identical nonces `d, e`, and signs `msg_A = set_keys_message(set, removed, key_pair_A)`, producing `s₁ = d + ρ·e + λ·x·c(H(msg_A), …)` [10](#0-9) .
3. A second call `share(preprocesses, key_pair_B)` (or `complete` with `key_pair_B`) under the same attempt reloads the same `S`, the same `d, e`, and signs `msg_B ≠ msg_A`, producing `s₂ = d + ρ·e + λ·x·c₂`.
4. Since the nonce commitments in `P` are identical, `s₁ − s₂ = λ·x·(c₁ − c₂)`; `λ` is public, so `x = (s₁ − s₂) / (λ·(c₁ − c₂))` — the signer's secret share is recovered.

Uncertainty: I could not fully trace all upstream callers that feed `key_pair` into `DkgConfirmer::share`/`complete` to confirm an attacker directly supplies two distinct key pairs under one attempt; the vulnerability stands on the deterministic reuse itself triggered by ordinary retry/re-execution paths.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L274-277)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
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

**File:** crypto/frost/src/sign.rs (L312-379)
```rust
    let view = self.params.keys.view(included.clone()).unwrap();
    validate_map(&preprocesses, &included, multisig_params.i())?;

    {
      // Domain separate FROST
      self.params.algorithm.transcript().domain_separate(b"FROST");
    }

    let nonces = self.params.algorithm.nonces();
    #[allow(non_snake_case)]
    let mut B = BindingFactor(HashMap::<Participant, _>::with_capacity(included.len()));
    {
      // Parse the preprocesses
      for l in &included {
        {
          self
            .params
            .algorithm
            .transcript()
            .append_message(b"participant", C::F::from(u64::from(u16::from(*l))).to_repr());
        }

        if *l == self.params.keys.params().i() {
          let commitments = self.preprocess.commitments.clone();
          commitments.transcript(self.params.algorithm.transcript());

          let addendum = self.preprocess.addendum.clone();
          {
            let mut buf = vec![];
            addendum.write(&mut buf).unwrap();
            self.params.algorithm.transcript().append_message(b"addendum", buf);
          }

          B.insert(*l, commitments);
          self.params.algorithm.process_addendum(&view, *l, addendum)?;
        } else {
          let preprocess = preprocesses.remove(l).unwrap();
          preprocess.commitments.transcript(self.params.algorithm.transcript());
          {
            let mut buf = vec![];
            preprocess.addendum.write(&mut buf).unwrap();
            self.params.algorithm.transcript().append_message(b"addendum", buf);
          }

          B.insert(*l, preprocess.commitments);
          self.params.algorithm.process_addendum(&view, *l, preprocess.addendum)?;
        }
      }

      // Re-format into the FROST-expected rho transcript
      let mut rho_transcript = A::Transcript::new(b"FROST_rho");
      rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes());
      rho_transcript.append_message(b"message", C::hash_msg(msg));
      rho_transcript.append_message(
        b"preprocesses",
        C::hash_commitments(self.params.algorithm.transcript().challenge(b"preprocesses").as_ref()),
      );

      // Generate the per-signer binding factors
      B.calculate_binding_factors(&rho_transcript);

      // Merge the rho transcript back into the global one to ensure its advanced, while
      // simultaneously committing to everything
      self
        .params
        .algorithm
        .transcript()
        .append_message(b"rho_transcript", rho_transcript.challenge(b"merge"));
```
