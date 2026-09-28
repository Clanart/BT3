### Title
Nonce reuse in `DkgConfirmer` via re-signing inside `complete()` enables secret key share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The CVE-2024-31822 class is "attacker-controlled input is persisted and later interpreted in a privileged context." In Serai's coordinator, the analog is the deterministic `CachedPreprocess`: a nonce seed is stored in `CachedPreprocesses` keyed only by `(b"DkgConfirmer", attempt)` and replayed into `AlgorithmSignMachine::from_cache` on every call to `preprocess_internal`. `DkgConfirmer::complete` calls `share_internal` (and therefore `sign`) a second time, re-deriving the same FROST nonces. Because the preprocess set fed to each `sign` call is parsed from attacker-submitted transaction data, two invocations can commit the same nonces to different binding-factor sets / signing sets, yielding two shares under one nonce — the standard FROST nonce-reuse key-recovery equation. [1](#0-0) [2](#0-1) 

### Finding Description
`preprocess_internal` never rotates or deletes the cached seed: if `CachedPreprocesses::get` returns a value it is decrypted and passed to `from_cache`, which deterministically regenerates identical nonces via `ChaCha20Rng::from_seed`. [3](#0-2)  `share()` calls `share_internal` → `machine.sign(preprocesses, msg)`, and `complete()` calls `share_internal` again with independently parsed `preprocesses` before calling `complete_internal`. [4](#0-3)  The file's own header admits the danger ("it is explicitly unsafe to reuse nonces across signing sessions... the received nonce commitments... would have to be distinct and sign would have to be called again") and lists a still-open TODO to verify the decided commitments match the on-chain preprocesses before publishing shares — that check is not implemented. [5](#0-4)  Inside `sign`, the share is `base + rho·actual` where `rho` is bound to the *per-call* preprocess transcript (`hash_commitments` over the participants' commitments), so identical nonces with distinct preprocess sets produce distinct `rho` values. [6](#0-5) 

### Impact Explanation
`s₁ = d + ρ₁·e + λ₁·c·share` and `s₂ = d + ρ₂·e + λ₂·c·share` over the same nonce commitments `(d, e)` but different binding factors/Lagrange coefficients lets an observer solve the 2×2 system and recover the validator's MuSig secret share — i.e., the validator root-of-trust key used to confirm DKG results on Substrate (`set_keys_message`). That is private key share recovery from publicly broadcast signature shares, a critical compromise.

### Likelihood Explanation
The preprocess sets in `share()` and `complete()` come from `HashMap<Participant, Vec<u8>>` decoded out of tributary transactions that any validator participant can author. A participant who publishes their preprocess in one configuration for the share phase and a different signer set/preprocess encoding in the transaction carrying shares causes the coordinator's `complete()` to re-sign the same message family under recycled nonces. No collusion threshold or broken BFT is required — a single malicious (or even buggy/duplicated-transaction) participant suffices.

### Recommendation
Before re-signing in `complete_internal`'s path, verify the decrypted cached preprocess's commitments serialize to exactly the local preprocess already published for this context, and abort (rather than call `sign` again) on mismatch — implementing the TODO at lines 50–54. Additionally, store a "share published" flag per context and refuse a second `sign` unless the preprocess map and message are byte-identical to the first.

### Proof of Concept
1. Validator V publishes `DkgCommitments` and a preprocess set S₁; coordinator `share()` produces share s₁ with nonces derived from `CachedPreprocesses[("DkgConfirmer", attempt)]`.
2. V (or another participant) causes a second transaction whose parsed `preprocesses` map S₂ ≠ S₁ (different participant subset ≥ t, or different commitment bytes for one signer) to be finalized.
3. `DkgConfirmer::complete(S₂, key_pair, shares)` re-executes `share_internal`, regenerates identical nonces, and emits s₂ with different binding factors `ρ`.
4. From s₁, s₂, the public commitments, and the Lagrange coefficients, solve for V's (or the coordinator's own) secret share; `schnorrkel` signature shares are linear, so two equations over one nonce pair recover the scalar share directly.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L25-54)
```rust
  As for safety, it is explicitly unsafe to reuse nonces across signing sessions. This raises
  concerns regarding our re-execution which is dependent on fixed nonces. Safety is derived from
  the nonces being context-bound under a BFT protocol. The flow is as follows:

  1) Decide the nonce.
  2) Publish the nonces' commitments, receiving everyone elses *and potentially the message to be
     signed*.
  3) Sign and publish the signature share.

  In order for nonce re-use to occur, the received nonce commitments (or the message to be signed)
  would have to be distinct and sign would have to be called again.

  Before we act on any received messages, they're ordered and finalized by a BFT algorithm. The
  only way to operate on distinct received messages would be if:

  1) A logical flaw exists, letting new messages over write prior messages
  2) A reorganization occurred from chain A to chain B, and with it, different messages

  Reorganizations are not supported, as BFT is assumed by the presence of a BFT algorithm. While
  a significant amount of processes may be byzantine, leading to BFT being broken, that still will
  not trigger a reorganization. The only way to move to a distinct chain, with distinct messages,
  would be by rebuilding the local process (this time following chain B). Upon any complete
  rebuild, we'd re-decide nonces, achieving safety. This does set a bound preventing partial
  rebuilds which is accepted.

  Additionally, to ensure a rebuilt service isn't flagged as malicious, we have to check the
  commitments generated from the decided nonces are in fact its commitments on-chain (TODO).

  TODO: We also need to review how we're handling Processor preprocesses and likely implement the
  same on-chain-preprocess-matches-presumed-preprocess check before publishing shares.
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

**File:** crypto/frost/src/sign.rs (L362-396)
```rust
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
    }

    #[allow(non_snake_case)]
    let Rs = B.nonces(&nonces);

    let our_binding_factors = B.binding_factors(multisig_params.i());
    let nonces = self
      .nonces
      .drain(..)
      .enumerate()
      .map(|(n, nonces)| {
        let [base, mut actual] = nonces.0;
        *actual *= our_binding_factors[n];
        *actual += base.deref();
        actual
      })
      .collect::<Vec<_>>();
```
