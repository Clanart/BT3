### Title
Reused deterministic FROST nonces when `DkgConfirmer::complete` re-derives the consumed signing machine - ([File: coordinator/src/tributary/signing_protocol.rs](coordinator/src/tributary/signing_protocol.rs))

### Summary
The kernel bug class in CVE-2022-1679 is state that continues to be used after a consuming/failing operation (use-after-free driven by attacker-controlled input). Serai's structural analog lives in the coordinator's MuSig confirmation signing protocol: `DkgConfirmer::share()` consumes an `AlgorithmSignMachine` (draining its secret nonces), and `DkgConfirmer::complete()` then calls `share_internal` again, which rebuilds a fresh machine from the *same* `CachedPreprocesses` DB seed. Because `seeded_preprocess` derives all nonces deterministically from that seed via `ChaCha20Rng`, the second call re-materializes and re-uses nonces that were already spent — a logical use-after-free of nonce state.

### Finding Description
`preprocess_internal` stores a 32-byte seed in `CachedPreprocesses` keyed only by `context = (b"DkgConfirmer", attempt)` and regenerates identical nonces on every call: [1](#0-0) 

The seed → nonce mapping is fully deterministic: [2](#0-1) 

`share()` produces and broadcasts a share once; `complete()` then calls `share_internal` a second time with attacker-influenced `preprocesses` bytes taken from the tributary transaction: [3](#0-2) 

If the `preprocesses` map passed to `complete` differs from the one used for `share` (the map contents come from on-chain transaction data supplied by participants), the binding factor `rho` for this signer changes — the code explicitly re-derives `rho` over `group_key`, `hash_msg`, and the transcripted preprocesses — while the underlying nonce pair `(base, actual)` stays identical: [4](#0-3) 

The recomputed share under the second `rho` is folded into the aggregate signature (`responses.insert(params.i(), self.share)` and `sum += share.0`), so an observer who knows the other participants' shares can recover this signer's second share as `s_i' = s_agg − Σ s_j`: [5](#0-4) 

### Impact Explanation
Two shares produced under the same nonce with different binding factors is the classic FROST/MuSig nonce-reuse (ROS-style) break: `s − s' = (rho − rho')·e + share·c·(λ − λ')`. With the same signing set (`λ = λ'`), `e` is recovered directly, and then the private key share is recovered as `(s − d − rho·e)/(λ·c)`. This is private-key-share recovery, the exact consequence the in-code documentation warns about ("Reuse will enable third-party recovery of your private key share", sign.rs:85-87). The file's own safety argument (lines 34-48) only considers re-execution across rebuilds/BFT violations and explicitly lists the matching on-chain check as an unimplemented TODO (line 51), meaning the `share()`/`complete()` double-derivation within one attempt is unguarded.

### Likelihood Explanation
Triggering requires a `complete` transaction carrying a `preprocesses` set distinct from the one used when `share()` ran, plus a valid resulting aggregate — which generally requires cooperation of a threshold of preprocess authors (validators). Within one attempt the honest-path maps are identical, so honest execution yields identical shares and no leak. Severity: **Medium** — a real reuse-after-consumption flaw in nonce handling, but exploitation needs a threshold-coordinated difference in the preprocess sets rather than a purely unprivileged input.

### Recommendation
Do not re-run `share_internal` inside `complete`. Cache the `AlgorithmSignatureMachine` produced by `share()` (in memory or encrypted in the DB alongside `CachedPreprocesses`) and pass it to `complete`, or delete/invalidate the `CachedPreprocesses` entry on first `sign` so any second derivation is impossible. Additionally implement the noted TODO: verify the presumed preprocess equals the on-chain preprocess before publishing shares.

### Proof of Concept
1. Attempt N of the DKG confirmation runs; `share()` calls `preprocess_internal`, loads seed S from `CachedPreprocesses[(b"DkgConfirmer", N)]`, derives nonces `(d, e)`, and emits share `s_A` under binding factor `rho_A`.
2. A `complete` transaction is finalized carrying a modified `preprocesses` map (e.g., a different preprocess for some participant), producing `rho_B ≠ rho_A` for this signer.
3. `complete()` → `share_internal` → `preprocess_internal` reloads seed S, regenerates identical `(d, e)`, and computes `s_B = d + rho_B·e + λ·c·share`, which enters the published aggregate signature.
4. Attacker computes `s_B = s_agg − Σ_{j≠i} s_j`, then `e = (s_A − s_B)/(rho_A − rho_B)`, `d = s_A − rho_A·e − λ·c·share` — recovering the private key share once `share` is isolated from `s_A − d − rho_A·e = λ·c·share`.

Caveat: I could not fully trace the call sites in `handle.rs`/`transaction.rs` (grep returned only match counts, not snippets), so whether honest flows can also invoke `share()` twice with divergent preprocess maps within one attempt is unverified; the confirmed reuse is the `share()` → `complete()` double `share_internal`.

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

**File:** crypto/frost/src/sign.rs (L127-141)
```rust
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

**File:** crypto/frost/src/sign.rs (L454-466)
```rust
    let mut responses = HashMap::new();
    responses.insert(params.i(), self.share);
    let mut sum = self.share;
    for (l, share) in shares.drain() {
      responses.insert(l, share.0);
      sum += share.0;
    }

    // Perform signature validation instead of individual share validation
    // For the success route, which should be much more frequent, this should be faster
    // It also acts as an integrity check of this library's signing function
    if let Some(sig) = self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum) {
      return Ok(sig);
```
