### Title
Stale `CachedPreprocess` reuse across rebuilds reuses FROST nonces, enabling validator key-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The coordinator's `SigningProtocol` deterministically regenerates FROST signing nonces from a seed persisted in `CachedPreprocesses` keyed only by `context` (e.g. `("DkgConfirmer", attempt)`). The module's own safety argument claims nonces are "re-decided" upon any rebuild, but the seed survives in the database, so a rebuilt coordinator reuses the identical binomial nonces `(d, e)`. If the rebuilt chain commits a different participant preprocess set or a different `KeyPair`, `share_internal` signs a different message/binding set under the same nonce, and any observer of the two published `[u8; 32]` shares recovers the signer's secret — the validator's root-of-trust Ristretto key share. This is the Serai analog of a use-after-free: nonce state that is presumed consumed/discarded is silently resurrected from stale storage and reused. [1](#0-0) 

### Finding Description
- `preprocess_internal` loads `CachedPreprocesses::get(txn, context)`; only if absent does it generate a fresh seed via `preprocess(&mut OsRng)` and store it (XOR-"encrypted" with a key derived from the private key and context). It then rebuilds the signing machine via `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))`. [1](#0-0) 
- `from_cache` → `seeded_preprocess` seeds `ChaCha20Rng::from_seed(*seed.0)`, so the same seed deterministically reproduces the same `nonces` and `commitments`. Nothing is consumed or deleted after signing; the seed row remains for the lifetime of the context key. [2](#0-1) 
- Both `DkgConfirmer::share` and `DkgConfirmer::complete` funnel into `share_internal`, which calls `self.preprocess_internal(participants).0` — meaning every call re-derives the same nonces. `share` and `complete` are invoked at different points over messages that are only presumed identical. [3](#0-2) 
- The header comment explicitly justifies fixed nonces by asserting "Upon any complete rebuild, we'd re-decide nonces, achieving safety." That assertion is false for the DB-cached path: the seed is never re-decided, it is replayed. A rebuild following a chain reorganization — or any divergence where the finalized tributary history carries different `Preprocess`/`Share` messages or a different `KeyPair` for the same `attempt` — produces a second signature share `s' = d + e·rho' + lambda·c'·sk` under the same `d, e` while the attacker-chosen preprocesses change `rho'` (computed via `hash_binding_factor` over the transcripted preprocess set) and the challenge `c'`. [4](#0-3) 
- The binding factor `rho` is a per-participant hash over `hash_msg(msg)` and the transcript of all preprocesses (`nonce.rs:161-172`, `sign.rs:361-371`), so two distinct committed preprocess sets yield different effective nonces only on the peer side — our side's raw `d, e` scalars are identical. [5](#0-4) 

### Impact Explanation
With two shares `s = d + e·rho + λ·c·x` and `s' = d + e·rho' + λ·c'·x` (same `d, e`, same Lagrange coefficient `λ` since our index is fixed, but different `rho, rho'` and different challenge `c, c'` — or even the same `c` with different `rho`), the attacker obtains two linear equations in three unknowns; combined with the public commitment `D, E` (our published preprocess, unchanged) and the group key, the standard two-equation FROST nonce-reuse solve recovers our secret share `x` whenever `c ≠ c'` or the message differs, and trivially when the peers' contributions shift `rho`. Recovered `x` is the validator's long-lived MuSig/Ristretto key — the root of trust used to confirm DKG results on-chain (`set_keys_message`). An attacker who recovers it can forge future DKG confirmations and sign `set_keys` for attacker-controlled key pairs, i.e., capture the threshold key being installed. This satisfies "key share recovery" and "concrete signing of an unintended message." [6](#0-5) 

### Likelihood Explanation
Reachability requires a rebuild/resync of the coordinator where the DB `CachedPreprocesses` row persists but the finalized tributary transcript differs — e.g., after a reorganization of the underlying chain, DB rollback/restore, or any path where `DkgConfirmer::share` and `::complete` are executed against preprocess maps that differ (the preprocesses are attacker-supplied bytes parsed via `read_preprocess`). The code comments themselves flag this as a concern ("TODO: review how we're handling Processor preprocesses"), and the stated safety mechanism (re-deciding nonces on rebuild) is not implemented — the cache is keyed only by context, so determinism is guaranteed, not prevented. An unprivileged participant controls the preprocess bytes and can also influence which attempt/context is in flight, and the ROI is maximal: one reused-nonce pair exposes the validator key. High. [7](#0-6) 

### Recommendation
- Do not reuse the cached seed once a share has been produced: record a "spent" flag alongside `CachedPreprocesses` and refuse to sign twice under the same context, or delete the row on first `share_internal` and require re-derivation only for identical, hash-verified inputs.
- Before signing from a cached seed, verify on-chain that the commitments previously published for this context match the commitments `seeded_preprocess` regenerates (the file's own TODO), and that the message/preprocess set matches what was already signed — abort otherwise.
- Alternatively, mix a monotonic attempt-specific nonce or the digest of the committed preprocess set into the seed derivation so any divergence yields fresh nonces rather than identical ones. [1](#0-0) 

### Proof of Concept
1. Validator V runs `DkgConfirmer::share(preprocesses_A, key_pair_A)` for `context = ("DkgConfirmer", 1)`. Internally, `preprocess_internal` stores seed `S` in `CachedPreprocesses` and signs with nonces `(d, e) = ChaCha20Rng(S)`; share `s1` is broadcast.
2. The coordinator process is rebuilt (restore from DB backup, resync onto a divergent tributary history, or a second `complete`/`share` path fed a different committed `Preprocess`/`KeyPair` set — all attacker-influenced inputs).
3. `DkgConfirmer::share`/`complete` executes again for the same context. `CachedPreprocesses::get` returns `S`; `from_cache` regenerates identical `(d, e)`. The new call signs `msg' = set_keys_message(..., key_pair_B)` (or the same `msg` but a preprocess set producing different `rho` per `hash_binding_factor`), emitting share `s2` under the same nonce commitments `D = d·G, E = e·G`, which are byte-identical to round 1.
4. Attacker solves `s1 = d + e·rho1 + λ·c1·x`, `s2 = d + e·rho2 + λ·c2·x`. With `rho1, rho2, c1, c2, λ` all publicly computable, this is two equations in `(d, e, x)`; adding the public-key equation `X = x·G` (or a third share if `complete` re-signs again) recovers `x` directly — or, in the common sub-case where the same participant set yields `rho1 = rho2`, plain subtraction `s1 − s2 = λ·(c1 − c2)·x` immediately yields `x`.
5. Recovered `x` is the validator's root-of-trust key, enabling forged DKG confirmations / `set_keys` for attacker-chosen `KeyPair`s. [8](#0-7)

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L34-55)
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

  Additionally, to ensure a rebuilt service isn't flagged as malicious, we have to check the
  commitments generated from the decided nonces are in fact its commitments on-chain (TODO).

  TODO: We also need to review how we're handling Processor preprocesses and likely implement the
  same on-chain-preprocess-matches-presumed-preprocess check before publishing shares.
*/
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

**File:** coordinator/src/tributary/signing_protocol.rs (L296-301)
```rust
    let msg = set_keys_message(
      &self.spec.set(),
      &self.removed.iter().map(|key| Public::from(key.to_bytes())).collect::<Vec<_>>(),
      key_pair,
    );
    self.signing_protocol().share_internal(&participants, preprocesses, &msg)
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

**File:** crypto/frost/src/sign.rs (L121-145)
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

**File:** crypto/frost/src/sign.rs (L361-379)
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
```

**File:** crypto/frost/src/nonce.rs (L161-191)
```rust
  pub(crate) fn calculate_binding_factors<T: Clone + Transcript>(&mut self, transcript: &T) {
    for (l, binding) in &mut self.0 {
      let mut transcript = transcript.clone();
      transcript.append_message(b"participant", C::F::from(u64::from(u16::from(*l))).to_repr());
      // It *should* be perfectly fine to reuse a binding factor for multiple nonces
      // This generates a binding factor per nonce just to ensure it never comes up as a question
      binding.binding_factors = Some(
        (0 .. binding.commitments.nonces.len())
          .map(|_| C::hash_binding_factor(transcript.challenge(b"rho").as_ref()))
          .collect(),
      );
    }
  }

  pub(crate) fn binding_factors(&self, i: Participant) -> &[C::F] {
    self.0[&i].binding_factors.as_ref().unwrap()
  }

  // Get the bound nonces for a specific party
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
  }
```
