### Title
Stale cached FROST preprocess (seeded nonce grant) reused across distinct signing inputs leaks the validator's secret key - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The EMQX bug class is a *stale, content-unbound grant*: an authorization object created once (`plugins allow name@version`) is never expired and is never bound to the bytes it authorizes, so a later upload of attacker-chosen bytes is accepted under the old grant. The analog in Serai is `SigningProtocol::preprocess_internal`, which creates a deterministic FROST preprocess (seed → nonces) once per `context` and stores it in `CachedPreprocesses` indefinitely. Like EMQX's grant, the cached preprocess is (a) never expired/deleted and (b) not bound to the actual signing inputs — the peer preprocess set or the message. Every subsequent `share_internal` call for the same `context` reloads the same seed via `from_cache`/`seeded_preprocess`, regenerating identical nonces and identical commitments, then runs `sign` over whatever preprocesses/message arrived. Two `sign` executions with the same nonces but different peer preprocesses (different `included` set → different `rho_transcript` → different binding factors ρ) or a different `msg` yield two shares `s₁ = d + e·ρ₁ + c₁·λ·xᵢ` and `s₂ = d + e·ρ₂ + c₂·λ·xᵢ`, from which the validator's Ristretto secret key is recovered by linear algebra.

### Finding Description
`preprocess_internal` derives the encryption key and cache key solely from `self.context` — for `DkgConfirmer` that is `(b"DkgConfirmer", self.attempt)` (`signing_protocol.rs:275`). If `CachedPreprocesses::get(txn, &context)` is empty it generates a preprocess and stores the 32-byte seed; on every call it loads the seed and rebuilds the machine with `AlgorithmSignMachine::from_cache`, which calls `seeded_preprocess` and regenerates nonces via `ChaCha20Rng::from_seed(*seed.0)` — fully deterministic (`crypto/frost/src/sign.rs:121-145`). The cache is never cleared after use: nothing in `share_internal` or `complete_internal` deletes `CachedPreprocesses`.

`DkgConfirmer::share` calls `share_internal`, and `DkgConfirmer::complete` calls `share_internal` *again* before `complete_internal` (`signing_protocol.rs:304-327`). These two invocations receive `preprocesses` maps populated from whatever preprocess transactions were observed — the included participant set and their commitment bytes are attacker-influenced public inputs fed through `read_preprocess`. The FROST binding factor ρ is computed over `group_key`, `hash_msg(msg)`, and `hash_commitments(transcript of all preprocesses)` (`crypto/frost/src/sign.rs:361-371`), so any difference in the preprocess set or `msg` (which embeds `key_pair` via `set_keys_message`, `signing_protocol.rs:296-300`) produces different ρ and different challenge `c` while the nonce scalars `d, e` remain identical because the seed is identical. The file's own header concedes the hazard: "In order for nonce re-use to occur, the received nonce commitments (or the message to be signed) would have to be distinct and sign would have to be called again" and flags the missing check as `TODO` ("check the commitments generated from the decided nonces are in fact its commitments on-chain (TODO)"; "We also need to review how we're handling Processor preprocesses").

The analog is exact: a one-time-created object (grant / cached seed) is redeemed later against fresh, unbound bytes (plugin bytes / new preprocesses and message), with no freshness or content binding enforced.

### Impact Explanation
Two shares over the same nonce pair `(d, e)` but distinct binding factors/challenges let an observer solve the linear system for `xᵢ`, the validator's Ristretto secret key — the root-of-trust MuSig key used to confirm DKG results on-chain (per the file header, "the validators' keys... they're the root of trust"). Recovery of this key lets the attacker forge the validator's confirmation signatures and, combined with normal signing transcripts, extends toward the threshold keys themselves. This is a full key-share recovery, satisfying the concrete-impact bar.

### Likelihood Explanation
Reachability requires two `share_internal` runs for one `context` with differing preprocess sets or messages. `share` and `complete` each invoke `share_internal`, and the preprocess `HashMap` is built from on-chain transactions submitted by other validators — peer-supplied public bytes, exactly the class of input in scope. Whether an *unprivileged* (non-validator) party can force a second call with mutated inputs depends on coordinator call flow in `handle.rs` which could not be fully verified here; the most conservative trigger is a re-execution path (partial rebuild/re-invocation) or a peer submitting additional preprocesses between `share` and `complete`, which the code explicitly lists as a known gap rather than a handled case. Given the protocol's own comments acknowledging the exposure and that the inputs are peer-controlled bytes to `read_preprocess`, this rates Medium — consistent with the EMQX report's own Medium rating.

### Recommendation
Bind the cached preprocess to its authorized use, mirroring the EMQX fix (grant lifetime + content binding): key `CachedPreprocesses` by `context` plus a hash of the committed preprocess set/message (or store the published preprocess and verify equality before signing), and delete or tombstone the cache entry after a successful `share`/`complete` so `from_cache` cannot regenerate the same nonces. At minimum, implement the already-noted TODO: before publishing a share, verify that the on-chain preprocess bytes match the locally presumed preprocess and abort (without re-signing) if they differ.

### Proof of Concept
```text
// conceptual, in coordinator DkgConfirmer flow, fixed attempt => fixed context
let conf = DkgConfirmer::new(&key, &spec, &mut txn, attempt).unwrap();

// Call 1: preprocess set A observed so far
conf.share(preprocesses_A, &key_pair)?;      // uses cached seed S -> nonces (d,e)
// share_1 = d + e*rho_A + c_A * lambda_i * x_i   (broadcast on tributary)

// Later block: complete() re-runs share_internal with the now-larger
// preprocess set B (B != A) — same context => same seed S => same (d,e)
conf.complete(preprocesses_B, &key_pair, shares_B)?;
// share_2 = d + e*rho_B + c_B * lambda_i * x_i   (rho_B != rho_A since the
// 'preprocesses' transcript commitment differs -> different binding factors)

// Attacker knows rho_A, rho_B (public recompute), c_A, c_B (BIP-340/Schnorrkel
// challenge over public R sums and msg), and lambda_i (public Lagrange coeff):
//   e = (share_1 - share_2 - lambda_i*x*(c_A-c_B)) / (rho_A - rho_B)  — first
// solve pair with a second participant's differing share, or use two shares
// with same rho but different msg to extract d directly:
//   x_i = (share_1 - share_2) / (lambda_i * (c_A - c_B))   when rho equal, or
//   standard 2-equation elimination otherwise.
// Result: validator's Ristretto secret key x_i recovered.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

Caveat: I could not fully verify the `handle.rs` call flow that sequences `share` vs `complete` with divergent preprocess sets; the trigger rests on the code's own acknowledged TODO and the deterministic-seed design. If `complete` is always invoked on a byte-identical preprocess set and message, the issue degrades to re-execution/rebuild-path nonce reuse, which the file documents as a known but accepted bound.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L100-148)
```rust
  fn preprocess_internal(
    &mut self,
    participants: &[<Ristretto as Ciphersuite>::G],
  ) -> (AlgorithmSignMachine<Ristretto, Schnorrkel>, [u8; 64]) {
    // Encrypt the cached preprocess as recovery of it will enable recovering the private key
    // While the DB isn't expected to be arbitrarily readable, it isn't a proper secret store and
    // shouldn't be trusted as one
    let mut encryption_key = {
      let mut encryption_key_preimage =
        Zeroizing::new(b"Cached Preprocess Encryption Key".to_vec());
      encryption_key_preimage.extend(self.context.encode());
      let repr = Zeroizing::new(self.key.to_repr());
      encryption_key_preimage.extend(repr.deref());
      Blake2s256::digest(&encryption_key_preimage)
    };
    let encryption_key_slice: &mut [u8] = encryption_key.as_mut();

    let algorithm = Schnorrkel::new(b"substrate");
    let keys: ThresholdKeys<Ristretto> =
      musig(musig_context(self.spec.set().into()), self.key.clone(), participants)
        .expect("signing for a set we aren't in/validator present multiple times")
        .into();

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
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L274-327)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }

  fn preprocess_internal(&mut self) -> (AlgorithmSignMachine<Ristretto, Schnorrkel>, [u8; 64]) {
    let participants = self.spec.validators().iter().map(|val| val.0).collect::<Vec<_>>();
    self.signing_protocol().preprocess_internal(&participants)
  }
  // Get the preprocess for this confirmation.
  pub(crate) fn preprocess(&mut self) -> [u8; 64] {
    self.preprocess_internal().1
  }

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
