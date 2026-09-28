### Title
FROST nonce reuse via re-executable DKG-confirmation signing — cached preprocess is never consumed and `share`/`complete` re-sign under a possibly different challenge - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
Analogous to `resolveProposal` being callable repeatedly because resolution isn't tracked, `SigningProtocol::share_internal`/`complete` rebuild the FROST `AlgorithmSignMachine` from a database-cached preprocess seed on every call, without recording or enforcing that the nonce has already been used to sign a given `(message, preprocess set)`. The cached seed under `CachedPreprocesses(context)` — where `context = (b"DkgConfirmer", attempt)` — is read, decrypted, and passed to `AlgorithmSignMachine::from_cache` every time, and is never deleted or bound to the message being signed [1](#0-0) . `DkgConfirmer::share` and `DkgConfirmer::complete` each call `share_internal`, which calls `preprocess_internal` again and then `machine.sign(preprocesses, msg)` with the same deterministically-derived nonces [2](#0-1) [3](#0-2) .

### Finding Description
FROST security requires each nonce pair `(d, e)` to be used for exactly one signing session. `seeded_preprocess` derives nonces from `ChaCha20Rng::from_seed(seed)`, so the same seed always produces the same nonces and commitments [4](#0-3) . The module documentation explicitly acknowledges this hazard: "it is explicitly unsafe to reuse nonces across signing sessions" and safety relies on the received nonce commitments and message being identical across re-executions [5](#0-4) . However, nothing binds the cached seed to a specific `preprocesses` set or `key_pair`: the `sign` call's binding factor and challenge are functions of the included participant set and message, both of which are passed as arguments to each invocation [6](#0-5) . The code itself flags the missing enforcement as a TODO: "we have to check the commitments generated from the decided nonces are in fact its commitments on-chain (TODO)" and the equivalent for Processor preprocesses [7](#0-6) . If `share` is invoked (publishing a signature share) and `complete` — or a later `share` — is invoked with even one additional/different participant preprocess or a different `KeyPair`, a second signature share is produced with the identical nonce but a different challenge/binding coefficient [8](#0-7) .

### Impact Explanation
Two Schnorr signature shares `s = d + b·e + λ·x·c` (resp. `s′` with `b′`/`c′`) over the same nonce commitments let anyone observing the published share and the resulting signature solve for the signer's secret share `x` — the classic FROST/Schnorr nonce-reuse key-recovery attack. Here the compromised secret is the validator's MuSig/Ristretto key share used for DKG confirmation (`musig(...)` over `self.key`), i.e., the validator root-of-trust key [9](#0-8) . The preprocess inputs are arbitrary serialized bytes supplied in tributary data messages (`serialized_preprocesses`), and the set included in `sign` depends on which preprocesses have arrived when each call executes [10](#0-9) .

### Likelihood Explanation
No consensus-breaking or collusion assumption is required: the trigger is that `share`/`complete`/`share`-again be invoked with non-identical `preprocesses` maps or `key_pair` values under the same `(b"DkgConfirmer", attempt)` context — e.g., a re-execution at a different block height after additional preprocesses were finalized, or a confirmation attempt fed a different `KeyPair`. The design explicitly re-executes from DB state rather than tracking that a nonce was already consumed, which is the same missing-state-flag root cause as the reference report. The only mitigations (BFT ordering, full rebuilds) are assumptions documented in the file, and the author notes the consistency check is unimplemented [11](#0-10) . Residual uncertainty: exploitation requires the validator's published share and a distinct second challenge to actually be produced; whether honest-only flows can reach that divergence depends on coordinator call ordering, but the code provides no guard preventing it.

### Recommendation
Delete (or mark consumed) the `CachedPreprocesses` entry the first time `share_internal` signs, and/or store alongside the seed a commitment to the exact `preprocesses` map and `msg` signed, refusing to produce a second share for a differing input under the same context — mirroring the fix in the reference report (flag proposals as resolved). Implement the already-noted TODO of verifying the on-chain preprocess/commitments match the presumed preprocess before publishing any share [7](#0-6) .

### Proof of Concept
1. Coordinator calls `DkgConfirmer::share(preprocesses_A, key_pair)` → publishes share `s₁` computed with nonces derived from seed `S` cached under `(b"DkgConfirmer", attempt)`.
2. Later (e.g., after an additional validator's preprocess is finalized, or a re-execution with a differing `KeyPair`), `DkgConfirmer::complete(preprocesses_B, key_pair′, shares)` internally calls `share_internal` again, reloading seed `S` and producing share `s₂` with identical nonce commitments but different binding factor/challenge.
3. From `s₁` (published on tributary) and `s₂`/`σ` (visible in the completed confirmation), solve the two-equation linear system for the validator's secret share `x`, fully compromising the MuSig confirmation key share.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L117-121)
```rust
    let algorithm = Schnorrkel::new(b"substrate");
    let keys: ThresholdKeys<Ristretto> =
      musig(musig_context(self.spec.set().into()), self.key.clone(), participants)
        .expect("signing for a set we aren't in/validator present multiple times")
        .into();
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

**File:** crypto/frost/src/sign.rs (L283-313)
```rust
  fn sign(
    mut self,
    mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>>,
    msg: &[u8],
  ) -> Result<(Self::SignatureMachine, SignatureShare<C>), FrostError> {
    let multisig_params = self.params.multisig_params();

    let mut included = Vec::with_capacity(preprocesses.len() + 1);
    included.push(multisig_params.i());
    for l in preprocesses.keys() {
      included.push(*l);
    }
    included.sort_unstable();

    // Included < threshold
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }

    let view = self.params.keys.view(included.clone()).unwrap();
    validate_map(&preprocesses, &included, multisig_params.i())?;
```
