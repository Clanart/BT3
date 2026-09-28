The external report describes ordering misuse: attacker-influenced effects applied before state is committed, letting stale state be re-consumed. In Serai's shape, the reachable analog is a signing nonce being re-consumed across sessions because the cached preprocess is never invalidated after use — the "state" (one-time nonce seed) is reused rather than updated, exactly the class of bug where stale state produces unintended signatures/key leakage.

### Title
Deterministic CachedPreprocess reuse across sign sessions enables secret share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` encrypts and stores a FROST `CachedPreprocess` keyed only by `self.context` in `CachedPreprocesses`, then re-reads that same cache and rebuilds the sign machine via `AlgorithmSignMachine::from_cache` on every subsequent call. Because the cache is only written when absent (`if CachedPreprocesses::get(...).is_none()`), every signing session for a given context derives the identical preprocess seed — hence identical secret nonces — while `share_internal` signs arbitrary attacker-influenced `msg` values. Two signatures produced under different messages with the same nonce allow recovery of the signer's secret share, contradicting `SignMachine::from_cache`'s documented requirement that a cached preprocess "MUST only be used once" and "must be deleted so it's never reused." [1](#0-0) [2](#0-1) 

### Finding Description
`preprocess_internal` lazily initializes `CachedPreprocesses` for the context once, then on every invocation decrypts the stored seed and calls `from_cache`, which routes through `seeded_preprocess` to deterministically regenerate the same nonces. [1](#0-0)  `share_internal` then feeds this machine attacker-controlled `msg` (and peer preprocesses) into `machine.sign`, producing a signature share whose nonce component is `base + rho * actual` derived from the reused seed. [3](#0-2) [4](#0-3)  The FROST spec for this crate is explicit that cached preprocess reuse "enables recovery of your private key share," and the code never deletes or rotates the entry after `sign` consumes it. [5](#0-4)  This is the direct analog of the Solidity bug: an external input (`msg`, analogous to the external call) is consumed before the consumed state (the one-time nonce seed, analogous to `lockups[to]`) is invalidated, letting the stale state be re-entered on the next call.

### Impact Explanation
Two distinct messages signed under the same context produce shares built on identical nonces. From share equations `s1 = r + c1·x` and `s2 = r + c2·x` with differing challenges (different msg/participant sets → different rho/binding), an observer solves for the share `x` linearly. Per the crate's own documentation, reuse "will presumably cause the signer to leak their secret share," collapsing the threshold signing security for that validator key and enabling forged signatures on future tributary messages. [6](#0-5) [7](#0-6) 

### Likelihood Explanation
Any flow that calls `share_internal` twice for the same `context` triggers it — and `share_internal` itself calls `preprocess_internal`, so even a single share plus a second signing attempt suffices. Messages and peer preprocesses are unprivileged public inputs (delivered as serialized bytes to `read_preprocess`/`sign`), so an unprivileged party who can cause or observe two distinct signing requests over the same context reaches this path with no validator privileges required. [8](#0-7) [9](#0-8) 

### Recommendation
After successfully consuming the cached seed in `preprocess_internal` (or on `sign` completion), delete the `CachedPreprocesses` entry and regenerate a fresh preprocess per session, matching the `SignMachine::from_cache` contract. Alternatively, key the cache by a per-session identifier (e.g., including the message/set hash) rather than `context` alone, and refuse to sign if an entry for an already-consumed seed is encountered.

### Proof of Concept
1. Drive `SigningProtocol::share_internal` for context `C` with participant set and message `m1`; obtain share `s1`.
2. Drive it again for the same `C` with a different message `m2`; `CachedPreprocesses::get` returns the same seed, `from_cache` regenerates the same nonces, yielding share `s2` over the same `r`.
3. From `s1 - s2 = (c1 - c2)·x` over the scalar field, recover `x = (s1 - s2)/(c1 - c2)` — the validator's secret share — using the publicly derivable challenges `c1`, `c2` from the rho transcript bound to `msg`. [10](#0-9)

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

**File:** crypto/frost/src/sign.rs (L226-241)
```rust
  /// Read a Preprocess message.
  ///
  /// Despite taking self, this does not save the preprocess. It must be externally cached and
  /// passed into sign.
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess>;

  /// Sign a message.
  ///
  /// Takes in the participants' preprocess messages. Returns the signature share to be broadcast
  /// to all participants, over an authenticated channel. The parties who participate here will
  /// become the signing set for this session.
  fn sign(
    self,
    commitments: HashMap<Participant, Self::Preprocess>,
    msg: &[u8],
  ) -> Result<(Self::SignatureMachine, Self::SignatureShare), FrostError>;
```

**File:** crypto/frost/src/sign.rs (L361-398)
```rust
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

    let share = self.params.algorithm.sign_share(&view, &Rs, nonces, msg);
```

**File:** crypto/frost/src/nonce.rs (L180-190)
```rust
  pub(crate) fn bound(&self, l: Participant) -> Vec<Vec<C::G>> {
    let mut res = vec![];
    for (i, (nonce, rho)) in
      self.0[&l].commitments.nonces.iter().zip(self.binding_factors(l).iter()).enumerate()
    {
      res.push(vec![]);
      for generator in &nonce.generators {
        res[i].push(generator.0[0] + (generator.0[1] * rho));
      }
    }
    res
```
