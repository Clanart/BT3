### Title
Deterministic context-bound preprocess reuse enables FROST nonce reuse across distinct `share`/`complete` invocations, leaking the validator secret share - ([File: coordinator/src/tributary/signing_protocol.rs])

### Summary
`SigningProtocol::preprocess_internal` derives the FROST preprocess from a `CachedPreprocess` seed keyed only by `context` (e.g. `(b"DkgConfirmer", attempt)`). `DkgConfirmer::share` and `DkgConfirmer::complete` each call `share_internal`, which re-runs `preprocess_internal` and `machine.sign(preprocesses, msg)`. Because the seed is cached in the DB, every call under the same context regenerates the *same* FROST nonces, while `msg` (the `set_keys_message` over `spec.set()`, `removed`, and `key_pair`) and the `preprocesses` map (which feed the binding factor `rho`) are caller-supplied and can differ between calls.

### Finding Description
- `preprocess_internal` loads `CachedPreprocesses::get(txn, &context)`; if present it decrypts and rebuilds the machine via `AlgorithmSignMachine::from_cache`, yielding identical nonces for every call with the same context [1](#0-0) .
- `DkgConfirmer::complete` invokes `self.share_internal(preprocesses, key_pair)` again, signing `set_keys_message(...)` a second time under the same cached nonce [2](#0-1) .
- The message signed is derived from the supplied `key_pair` and `removed` set, so a second invocation with a different `key_pair`/participant map produces a different `msg` and different binding factors, while the nonce stays fixed [3](#0-2) .
- In `crypto/frost`, `seeded_preprocess` derives all nonces purely from `ChaCha20Rng::from_seed(seed)` with no fresh entropy at `sign` time, so `from_cache` reproduces identical nonces deterministically [4](#0-3) .
- This is the direct analog of the incident's bug class: instead of fresh randomness per signing decision, a "random" value is derived from controllable/static inputs (context, not the message or session), so replaying a signing attempt with different attacker-influenced data reuses the nonce.

### Impact Explanation
FROST signature shares are linear in the nonce: `s = d + rho*e + c*share`. Two shares produced under the same nonce but different `rho`/challenge values (different `msg` or different preprocess sets) yield a linear system recovering the signer's secret share — full compromise of that validator's MuSig key and, aggregated across validators, the threshold key. The code comments acknowledge nonce reuse is "explicitly unsafe" and rely on BFT ordering for safety, but `complete()` unconditionally re-signs via `share_internal`, and any path (rebuild boundary, retry with corrected `key_pair`, competing preprocess sets) that changes `msg` or `preprocesses` between the two sign calls triggers reuse [5](#0-4) .

### Likelihood Explanation
Reachability requires two sign invocations under the same `context` with differing inputs — e.g., `share()` published for one `key_pair`/preprocess quorum, then `complete()` invoked on a re-executed or updated view producing a different `msg`. An unprivileged party supplying preprocess bytes/`key_pair` data that changes the signed message while the nonce stays cached can force this; no key compromise or protocol violation inside FROST is needed. Impact (secret share recovery) is high; trigger conditions depend on the coordinator's calling pattern, so Medium.

### Recommendation
- Key the cached preprocess seed by a commitment to everything that feeds `sign` (e.g., hash of `msg` + sorted preprocess set), or store and compare the produced preprocess commitments before signing — as the file's own TODO notes ("check the commitments generated from the decided nonces are in fact its commitments on-chain" is unimplemented) [6](#0-5) .
- Make `share_internal`/`complete` single-shot per context: refuse a second `sign` under a context whose prior share was emitted, or derive nonces as `hash(seed || msg || preprocesses)` so differing inputs yield distinct nonces.

### Proof of Concept
1. Within one `attempt`, call `DkgConfirmer::share(preprocesses_A, key_pair_A)` → obtains share `s1` over `msg_A` with cached nonce `d, e`.
2. Trigger `DkgConfirmer::complete(preprocesses_B, key_pair_B, shares)` where `key_pair_B != key_pair_A` (or differing `removed`/participant mapping) → `share_internal` re-signs `msg_B` under identical `d, e` → share `s2`.
3. Compute the signer's FROST share from `s1 = d + rho1*e + c1*x` and `s2 = d + rho2*e + c2*x` with publicly known `rho_i, c_i` (derived from broadcast commitments and `msg`), solving the 2x2 linear system for `x`.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L25-35)
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
```

**File:** coordinator/src/tributary/signing_protocol.rs (L50-54)
```rust
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

**File:** coordinator/src/tributary/signing_protocol.rs (L288-301)
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
