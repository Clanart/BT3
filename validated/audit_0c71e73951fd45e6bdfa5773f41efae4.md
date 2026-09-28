### Title
FROST nonce reuse across `share` and `complete` phases via deterministic cached preprocess enables validator key recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` deterministically rebuilds the FROST signing machine from a cached seed stored in `CachedPreprocesses` keyed only by `("DkgConfirmer", attempt)` [1](#0-0) . Both `DkgConfirmer::share` and `DkgConfirmer::complete` independently call `share_internal`, which calls `preprocess_internal` and then `machine.sign(...)` [2](#0-1) . If the `preprocesses` map passed to `complete` differs from the one passed to `share` (different participant set or different preprocess bytes), `sign` is executed twice with identical nonces under different binding factors — a nonce reuse that leaks the validator's private key. This mirrors CVE-2019-9637's class: an object (the cached nonce seed) is legitimately persisted for one phase but remains silently reusable in a later phase where the surrounding context has changed, violating the single-use invariant.

### Finding Description
- `preprocess_internal` generates a fresh seeded preprocess once per `context`, stores `machine.cache()` XORed with an encryption key, and on every subsequent call reconstructs `AlgorithmSignMachine::from_cache` from the same seed [3](#0-2) . The seed fully determines the FROST nonces, so every machine built for `("DkgConfirmer", attempt)` uses identical nonce scalars.
- `share` calls `share_internal(preprocesses, key_pair)` → `sign(preprocesses, msg)` [4](#0-3) .
- `complete` calls `share_internal(preprocesses, key_pair)` again — a second, independent `sign` with the same cached nonces — before completing the signature [5](#0-4) .
- The two calls take their `preprocesses` as independent parameters with no equality check. In `crypto/frost/src/sign.rs`, the binding factors `rho` are derived from the transcript of every included participant's commitments (`B.calculate_binding_factors(&rho_transcript)`), and the signature share is `d + rho*e + c*lam*share` [6](#0-5) . Two `sign` executions with the same `d, e` but different `included` sets or commitment values yield shares with different `rho`/`c`, allowing algebraic recovery of the secret share (the file's own header acknowledges: "it is explicitly unsafe to reuse nonces across signing sessions" and that reuse happens if "the received nonce commitments … would have to be distinct and sign would have to be called again" — which is exactly what `share`+`complete` does) [7](#0-6) .
- The safety argument in the header relies on BFT ordering giving identical inputs, but nothing enforces that `share` and `complete` receive the same `HashMap<Participant, Vec<u8>>`; a validator's own submitted `DkgShares` preprocess bytes are public tributary transaction data, and the set of preprocesses available when `share` runs versus when `complete` runs can naturally differ (e.g., a participant's `DkgShares` transaction arriving between the two calls changes the map's contents).

### Impact Explanation
Reuse of a Schnorr/FROST nonce `e` across two challenges with different binding factors yields two linear equations `s1 = d + rho1*e + c1*lam*x` and `s2 = d + rho2*e + c2*lam*x`; subtracting eliminates `d` and solving across the known `rho`/`c`/`lam` values recovers the validator's Ristretto private key — the same key used to confirm DKG results on-chain as the root-of-trust MuSig signer [8](#0-7) . Compromise of that key lets an attacker forge `DkgConfirmed` confirmation shares, undermining validator-set key confirmation.

### Likelihood Explanation
Exploitation requires that `share` and `complete` for the same `attempt` observe different preprocess maps. Since `complete` accepts preprocesses as an independent argument and preprocesses arrive via separate `DkgShares` transactions over multiple blocks, a difference is a realistic operational condition rather than an adversary-only corner. Any other validator (or anyone who observes the two published shares on the public tributary) can perform the recovery; an adversarial validator can additionally influence which preprocesses are present by timing its own `DkgShares` transaction.

### Recommendation
Make `sign` single-use per context: after `share_internal` produces a share for `("DkgConfirmer", attempt)`, record the exact serialized preprocesses used and either (a) have `complete` re-use the already-produced signature machine rather than re-signing, or (b) assert in `complete` that the preprocess map byte-for-byte equals the one used for `share` (analogous to the `assert_eq!(&existing, preprocess)` pattern in `FirstPreprocessDb`) [9](#0-8)  and abort otherwise. Additionally, implement the file's own TODO of verifying the on-chain preprocesses match the presumed cached preprocess before publishing shares [10](#0-9) .

### Proof of Concept
1. Validator set runs DKG attempt `a`; `DkgConfirmer::new(key, spec, txn, a)` yields a confirmer. `preprocess()` stores seed `S` in `CachedPreprocesses[("DkgConfirmer", a)]`.
2. `share(P1, key_pair)` is invoked with preprocess map `P1` containing participants `{1,2,3}` → `sign` consumes nonces derived from `S`, producing share `s1 = d + rho1*e + c1*lam*x`.
3. `complete(P2, key_pair, shares)` is invoked where `P2 = P1 ∪ {participant 4's preprocess}` (a `DkgShares` tx finalized after step 2) → `share_internal` rebuilds the machine from the same seed `S` → `sign` emits `s2 = d + rho2*e + c2*lam*x` with `rho2 ≠ rho1` and `c2 ≠ c1` because the rho transcript commits to every included participant's commitments [11](#0-10) .
4. Both `s1` and `s2` are public (published as `DkgConfirmed` confirmation shares / computable by `complete`). An observer solves the two-equation linear system over the known `rho1, rho2, c1, c2, lam` to recover `x`, the validator's MuSig secret key.

Uncertainty: I could not fully trace `handle.rs` to confirm the exact call ordering of `share` versus `complete` and whether the preprocess maps passed are guaranteed identical; the vulnerability holds whenever they can differ, and the code performs no check that they are equal.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L1-14)
```rust
/*
  A MuSig-based signing protocol executed with the validators' keys.

  This is used for confirming the results of a DKG on-chain, an operation requiring all validators
  which aren't specified as removed while still satisfying a supermajority.

  Since we're using the validator's keys, as needed for their being the root of trust, the
  coordinator must perform the signing. This is distinct from all other group-signing operations,
  as they're all done by the processor.

  The MuSig-aggregation achieves on-chain efficiency and enables a more secure design pattern.
  While we could individually tack votes, that'd require logic to prevent voting multiple times and
  tracking the accumulated votes. MuSig-aggregation simply requires checking the list is sorted and
  the list's weight exceeds the threshold.
```

**File:** coordinator/src/tributary/signing_protocol.rs (L25-41)
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

**File:** crypto/frost/src/sign.rs (L325-379)
```rust
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

**File:** coordinator/src/db.rs (L90-94)
```rust
    if let Some(existing) = FirstPreprocessDb::get(txn, network, id_type, id) {
      assert_eq!(&existing, preprocess, "saved a distinct first preprocess");
      return;
    }
    FirstPreprocessDb::set(txn, network, id_type, id, preprocess);
```
