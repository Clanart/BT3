### Title
Cached FROST preprocess is never consumed: `share`/`complete` re-derive the same nonces, enabling secret-share recovery if the preprocess set diverges — (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The kernel bug (CVE-2023-0030) is a use-after-free: a resource that was logically consumed is used again. The direct analog in Serai is FROST nonce reuse: a `CachedPreprocess` seed is stored in the DB under `context` and is *never deleted or marked consumed*. `SigningProtocol::share_internal` calls `preprocess_internal`, which deterministically rebuilds the `AlgorithmSignMachine` — including its secret nonces — from that seed every time it runs. `DkgConfirmer::share` and `DkgConfirmer::complete` each invoke `share_internal` with independently-supplied `preprocesses` maps, so the same secret nonces `(d, e)` are used to produce signature shares twice whenever the two preprocess sets differ.

### Finding Description
`preprocess_internal` caches only the ChaCha20 seed, keyed by `(b"DkgConfirmer", attempt)`, and reuses it on every call: [1](#0-0) 
`DkgConfirmer::complete` re-runs `share_internal` (and therefore `machine.sign(preprocesses, msg)`) with a `preprocesses` argument supplied separately from the one used in `share`: [2](#0-1) 
Inside `AlgorithmSignMachine::sign`, the secret nonce is `d + e·rho_i` where `rho_i` is a binding factor derived from the transcript of *all* participants' commitments: [3](#0-2) [4](#0-3) 
A co-signer who causes the commitment set seen at `share` time to differ from the set seen at `complete` time (e.g., by equivocating on its preprocess, or changing which participants are included) changes `rho_i` while `d` and `e` stay fixed. The two emitted shares are `s1 = d + e·rho1 + λ·c·sk` and `s2 = d + e·rho2 + λ·c·sk`; subtracting yields `e = (s1−s2)/(rho1−rho2)`, then `d`, then the secret share `sk`. `D`, `E` commitments are public, so `d`/`e` can be cross-checked.

The file's own commentary acknowledges the exposure: deterministic nonces are only safe because the BFT layer is assumed to deliver identical messages, and a TODO explicitly flags the missing "on-chain-preprocess-matches-presumed-preprocess check before publishing shares": [5](#0-4) 

### Impact Explanation
Recovery of a validator's MuSig secret key share. Since this `SigningProtocol` wraps `musig(...)` on the validator's own key (the root of trust for DKG confirmation), a recovered share contributes toward forging the validator-set confirmation signatures. Within `crypto/frost` generally, any caller that re-enters `from_cache`/`sign` with divergent preprocess maps leaks its `ThresholdKeys` secret share — exactly the "reuse will enable third-party recovery of your private key share" hazard the API documents but does not enforce: [6](#0-5) 

### Likelihood Explanation
Reachable by a single malicious participant in the signing set (no threshold collusion needed): the attacker supplies one preprocess in the round feeding `share` and a different one in the round feeding `complete`. The code itself relies on BFT message consistency for safety, and explicitly notes that partial rebuilds/re-execution violate that assumption — so the divergence need not even be adversarial; a crash/rebuild between the two rounds with different observed state triggers the same reuse. The crypto layer provides no defense: `from_cache` happily re-seeds identical nonces and `sign` drains them without checking the cache was consumed: [7](#0-6) 

### Recommendation
- Delete/tombstone `CachedPreprocesses` as soon as a share is produced for a context, and refuse to sign a second time under the same seed.
- Before publishing a share, verify the preprocess set is the one previously finalized for this attempt (the TODO's suggested on-chain-match check), so `complete` can never re-sign under different commitments.
- In `crypto/frost`, make `CachedPreprocess`/`from_cache` consumption enforced by the type system or a used-flag inside the seed encoding rather than a documented MUST.

### Proof of Concept
1. Validator V runs `DkgConfirmer::share(preprocesses_A, kp)` → `share_internal` rebuilds nonces `d,e` from `CachedPreprocesses["DkgConfirmer", attempt]` and emits `s1` over set A (binding factor `rho1`).
2. Attacker (another participant) arranges `DkgConfirmer::complete(preprocesses_B, kp, shares)` with `preprocesses_B ≠ preprocesses_A` (e.g., swaps in a different commitment for itself). `complete` calls `share_internal` again → same `d,e`, new `rho2`, internally producing `s2` — and `s2` equals the second share V would publish under set B.
3. Attacker computes `e = (s1 − s2)·(rho1 − rho2)⁻¹ mod ℓ` (Lagrange coefficient λ identical since the participant index set is unchanged), then `sk = (s1 − d − e·rho1)·(λ·c)⁻¹` after recovering `d` from the public commitment `D = d·G`.

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

**File:** crypto/frost/src/sign.rs (L268-287)
```rust
  fn from_cache(
    algorithm: A,
    keys: ThresholdKeys<C>,
    cache: CachedPreprocess,
  ) -> (Self, Self::Preprocess) {
    AlgorithmMachine::new(algorithm, keys).seeded_preprocess(cache)
  }

  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    Ok(Preprocess {
      commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
      addendum: self.params.algorithm.read_addendum(reader)?,
    })
  }

  fn sign(
    mut self,
    mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>>,
    msg: &[u8],
  ) -> Result<(Self::SignatureMachine, SignatureShare<C>), FrostError> {
```

**File:** crypto/frost/src/sign.rs (L361-396)
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
```

**File:** crypto/frost/src/nonce.rs (L161-173)
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
```
