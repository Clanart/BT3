### Title
Cached FROST preprocess seed is never deleted and is keyed only by `("DkgConfirmer", attempt)`, so nonces are deterministically reused across validator sets — ([File: coordinator/src/tributary/signing_protocol.rs](coordinator/src/tributary/signing_protocol.rs))

### Summary
The analog to "stake boost allocated to the next interval is never reset on unstake, letting repeat actions carry value into the next round" is `SigningProtocol::preprocess_internal`: a deterministic FROST nonce seed is written to `CachedPreprocesses` the first time a context is seen and is **never cleared nor rotated**, and the context key is only `(b"DkgConfirmer", self.attempt)` — it does not include the validator set, genesis, or session. Every other table in this module's DB layer is explicitly keyed by `genesis` (e.g. `AttemptDb::attempt(txn, genesis, topic)`), which indicates the DB is *not* implicitly scoped per tributary/set. Two DKG confirmations for different sets that reach the same attempt number therefore rebuild `AlgorithmSignMachine::from_cache` with the identical seed, producing identical nonce commitments and identical secret nonces while signing *different* `set_keys_message` payloads — classic Schnorr nonce reuse exposing the validator's private key.

### Finding Description
`preprocess_internal` computes an encryption key from `"Cached Preprocess Encryption Key" || context || key`, generates a preprocess once per context, XORs the seed with that key, and stores it in `CachedPreprocesses` — but only `if CachedPreprocesses::get(...).is_none()`, and nothing ever removes or rotates the entry [1](#0-0) . The context for the only consumer is `(b"DkgConfirmer", self.attempt)` — a static tag and a `u32` attempt counter [2](#0-1) . `from_cache` re-derives nonces deterministically via `seeded_preprocess`, where `ChaCha20Rng::from_seed(*seed.0)` fully determines the `Commitments`/nonces [3](#0-2) . The file's own header acknowledges the danger: "it is explicitly unsafe to reuse nonces across signing sessions... Safety is derived from the nonces being context-bound" — but the bound context lacks the set/genesis [4](#0-3) . The documented MUST — "the preprocess must be deleted so it's never reused" — is never honored by this code path [5](#0-4) .

### Impact Explanation
When a second validator set runs its DKG confirmation, its attempt counter restarts at the same values (`attempt` is per-set via `AttemptDb`, not global). The MuSig participant list and `set_keys_message` differ, but the nonce seed is identical, so the same nonce `d, e` produce shares `s = d + e·b + λ·key·c` under two different challenges `c` (the message changes the challenge and binding factors). Two published signature shares with identical nonces and different challenges yield the validator's MuSig secret share by linear algebra — full compromise of the coordinator's validator signing key, enabling forged `SignCompleted`/set-keys signatures. This requires no misbehavior by anyone: it triggers in honest re-execution across sets, exactly like the original bug where honest repeat stake/unstake inflates the next interval's boost because state was never reset.

### Likelihood Explanation
The trigger is routine protocol progression: every new validator set performs a DKG confirmation whose `DkgConfirmer` attempt starts from the same low numbers, and `share_internal`/`complete` both rebuild the machine from the unchanged cache [6](#0-5) . The only uncertainty is whether `serai_db`'s `create_db!`/`DbTxn` namespaces keys per genesis; the explicit `genesis` parameter threaded through every neighboring table (`DataDb`, `AttemptDb`, `DataReceived`) indicates it does not [7](#0-6) . Additionally, even within one set, `share()` then `complete()` re-running `share_internal` is only safe because all inputs are BFT-finalized — the authors flag the missing on-chain commitment check as a TODO [8](#0-7) .

### Recommendation
Include a globally-unique domain in the context: `context = (b"DkgConfirmer", spec.genesis(), self.attempt)` (and equivalently `set().session`), and delete the `CachedPreprocesses` entry after `complete_internal` succeeds so the seed cannot outlive its single signing session. Before publishing shares, verify the locally derived commitments match the preprocess already committed on-chain for that context (the existing TODO), so any accidental re-execution with divergent inputs is caught rather than leaking the key share.

### Proof of Concept
1. Validator set `S₁` completes a DKG and reaches `DkgConfirmation` attempt `a`. `DkgConfirmer::share` calls `preprocess_internal` with context `(b"DkgConfirmer", a)`, stores seed `k` in `CachedPreprocesses`, and broadcasts a share over nonces derived from `ChaCha20Rng::from_seed(k)` signing `set_keys_message(S₁, removed₁, key_pair₁)` [9](#0-8) .
2. A later set `S₂` (same coordinator DB, same validator key `self.key`) reaches DKG confirmation attempt `a`. `CachedPreprocesses::get` hits, no new seed is generated, and `from_cache` regenerates the identical nonce pair [10](#0-9) .
3. The coordinator publishes a second share signing `set_keys_message(S₂, removed₂, key_pair₂)` — same nonce scalar `k_d + b·k_e`, different challenge. An observer of the two public `Label::Share` tributary messages solves `s₁ − s₂ = λ·key·(c₁ − c₂)` for the MuSig secret share, recovering the validator's private key.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L25-48)
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

**File:** coordinator/src/tributary/signing_protocol.rs (L274-277)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L296-301)
```rust
    let msg = set_keys_message(
      &self.spec.set(),
      &self.removed.iter().map(|key| Public::from(key.to_bytes())).collect::<Vec<_>>(),
      key_pair,
    );
    self.signing_protocol().share_internal(&participants, preprocesses, &msg)
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

**File:** coordinator/src/tributary/handle.rs (L99-113)
```rust
    if DataDb::get(self.txn, genesis, data_spec, &signer.to_bytes()).is_some() {
      panic!("accumulating data for a participant multiple times");
    }
    let signer_shares = {
      let Some(signer_i) = self.spec.i(removed, signer) else {
        log::warn!("accumulating data from {} who was removed", hex::encode(signer.to_bytes()));
        return Accumulation::NotReady;
      };
      u16::from(signer_i.end) - u16::from(signer_i.start)
    };

    let prior_received = DataReceived::get(self.txn, genesis, data_spec).unwrap_or_default();
    let now_received = prior_received + signer_shares;
    DataReceived::set(self.txn, genesis, data_spec, &now_received);
    DataDb::set(self.txn, genesis, data_spec, &signer.to_bytes(), data);
```
