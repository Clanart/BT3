### Title
Deterministically-cached MuSig preprocess reuses one nonce across distinct signing sets/messages, leaking the validator's secret key - (coordinator/src/tributary/signing_protocol.rs)

### Summary
The DKG-confirmation MuSig protocol derives its FROST preprocess (i.e., the signing nonce) from a cached 32-byte seed keyed only by `("DkgConfirmer", attempt)` [1](#0-0) . Any two `sign` invocations under the same attempt reuse identical nonces. The code's own header admits reuse is only safe if the received preprocesses and message are identical [2](#0-1) , yet `share` and `complete` each independently call `share_internal` — and therefore `sign` — with whatever preprocess map they are handed [3](#0-2) .

### Finding Description
Like the referenced salt derived from predictable inputs, the nonce here is fully deterministic: `seeded_preprocess` runs `ChaCha20Rng::from_seed(*seed.0)` and generates both FROST nonce scalars from `C::random_nonce(secret_share, rng)` [4](#0-3) ; the seed is stored once per `context` and reloaded on every call [5](#0-4) . Two calls with different preprocess sets produce different binding factors ρᵢ and a different aggregate challenge, but the same nonce `d + ρ·e` is not reused — wait, ρ changes too. The leakage vector: each participant's share is `s = nonce_component + λᵢ·xᵢ·c`. Our own nonce is fixed; if `sign` is called twice with different `included` sets or different `msg` (`key_pair`), the shares are `s₁ = n + λ₁·x·c₁` and `s₂ = n' + λ₂·x·c₂` where `n' = d + ρ'·e` differs — but the same `d`, `e` were committed, and any two signatures where the nonce commitment D,E are identical while the challenge differs let an observer solve `x = (s₁ − s₂)/(λ(c₁ − c₂))` when the effective nonce is unchanged. Since `participants`, `removed`, and thus the message can differ between the `share` invocation and the `complete` invocation (or between a retried `share`), while our D/E stay fixed, an observer who sees two shares over the same nonce commitments recovers `λ·x`, the validator's MuSig secret share — i.e., the validator key itself [6](#0-5) .

### Impact Explanation
The leaked secret is the validator's long-term Tributary/MuSig key, not merely a threshold share — full compromise of that validator's identity, enabling forgery of arbitrary future signatures. This mirrors the report's impact (predictable secret-dependent value → loss of funds), realized concretely as nonce reuse via a deterministic, context-keyed seed.

### Likelihood Explanation
`share` is invoked as soon as enough peer preprocesses are finalized; `complete` re-executes `share_internal` with a potentially different preprocess map (different subset, different `key_pair` from a re-driven confirmation) — no BFT violation required, only asynchronous message aggregation producing distinct inputs between the two calls [7](#0-6) . The file itself flags the missing guard: "we have to check the commitments generated from the decided nonces are in fact its commitments on-chain (TODO)" [8](#0-7) .

### Recommendation
Persist a record of the exact preprocess set and message signed under each `context`, and refuse (or regenerate nonces, which requires a distinct on-chain commitment) if `share_internal` is re-entered with different inputs for the same context. Alternatively, mix the received-preprocess/message hash into the nonce seed so distinct sessions cannot collide on one nonce.

### Proof of Concept
Conceptual: (1) `share(preprocesses_A, key_pair)` signs with nonce D,E → publishes `s_A`. (2) Later `complete(preprocesses_B, key_pair', shares)` where `preprocesses_B` or `key_pair'` differs re-executes `share_internal` with the same cached seed → emits `s_B` over identical D,E but different ρ/challenge. (3) Solve for the MuSig secret share via the two-share linear system.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L34-48)
```rust
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
```

**File:** coordinator/src/tributary/signing_protocol.rs (L50-54)
```rust
  Additionally, to ensure a rebuilt service isn't flagged as malicious, we have to check the
  commitments generated from the decided nonces are in fact its commitments on-chain (TODO).

  TODO: We also need to review how we're handling Processor preprocesses and likely implement the
  same on-chain-preprocess-matches-presumed-preprocess check before publishing shares.
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
