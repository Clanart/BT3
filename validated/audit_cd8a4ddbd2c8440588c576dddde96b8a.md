### Title
Cached FROST preprocess seeds are not bound to the signing parameters that consume them - ([File: crypto/frost/src/sign.rs](crypto/frost/src/sign.rs))

### Summary
`AlgorithmSignMachine::from_cache` reconstructs a signing machine from a raw 32-byte `CachedPreprocess`, plus caller-supplied `Params` and `Keys`. The cached value is only the RNG seed; it does not commit to, nor is it checked against, the algorithm, transcript, nonce layout, threshold parameters, participant index, group key, or key material used when it was created.

### Finding Description
`AlgorithmMachine::seeded_preprocess` interprets `CachedPreprocess` as a `ChaCha20Rng` seed and derives the secret nonces and public preprocess from `self.params` [1](#0-0) . `cache()` returns only that seed, and `from_cache()` accepts independently supplied `params` and `keys` without any consistency check [2](#0-1) . The API documentation only says the cache must not be reused; it does not require it to be rebound to the identical `Params` and `Keys` [3](#0-2) .

This mirrors the cache-key bug class: a seed generated under one signing context can later be interpreted under another. The resulting preprocess is then transcripted into FROST’s binding-factor calculation, while the message, group key, and received preprocesses determine the effective nonce and signature share [4](#0-3) .

### Impact Explanation
If an application persists `CachedPreprocess` under a signing-session key that omits any of the algorithm or key dimensions—such as participant set, participant index, threshold parameters, scalar/offset, or algorithm/transcript version—a later load can deterministically regenerate nonce material under the wrong signing context. An attacker who can cause two distinct signing operations to consume the same cached seed can obtain two Schnorr-style shares derived from related nonce state. If the effective nonce repeats with different challenges or secret-share coefficients, the private share can be recovered from the public shares.

### Likelihood Explanation
The vulnerable condition requires an integrating application to retain or reload a `CachedPreprocess` across a context boundary. The primitive itself does not enforce the binding, so the risk is highest for integrations that treat the byte seed as safely reusable for “the same signer” rather than for one exact `(Params, Keys)` pair.

### Recommendation
Bind the cache to its consuming parameters. `CachedPreprocess` should contain, or be stored alongside, a hash of the serialized algorithm parameters, threshold parameters, participant index, group key, verification shares, scalar/offset, nonce-generator layout, and protocol version. `from_cache` should fail unless the supplied `Params` and `Keys` match that commitment. Applications should additionally delete the stored cache before using it.

### Proof of Concept
1. Create a `CachedPreprocess` with `AlgorithmMachine::new(algorithm_a, keys_a).preprocess(rng)` and call `cache()` on the resulting `AlgorithmSignMachine`.
2. Persist only the returned `[u8; 32]`.
3. Later call `AlgorithmSignMachine::from_cache(algorithm_b, keys_b, cached)` where either the algorithm’s nonce generators/transcript or `keys_b` differ in participant index, threshold parameters, scalar, or offset.
4. The call succeeds because `from_cache` only forwards the seed into `seeded_preprocess` and performs no cache-to-context validation [5](#0-4) .
5. The machine can then produce a signature share for attacker-supplied preprocesses and a message even though the nonce seed was created under a different signing context [6](#0-5) .

### Citations

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

**File:** crypto/frost/src/sign.rs (L257-274)
```rust
impl<C: Curve, A: Algorithm<C>> SignMachine<A::Signature> for AlgorithmSignMachine<C, A> {
  type Params = A;
  type Keys = ThresholdKeys<C>;
  type Preprocess = Preprocess<C, A::Addendum>;
  type SignatureShare = SignatureShare<C>;
  type SignatureMachine = AlgorithmSignatureMachine<C, A>;

  fn cache(self) -> CachedPreprocess {
    self.seed
  }

  fn from_cache(
    algorithm: A,
    keys: ThresholdKeys<C>,
    cache: CachedPreprocess,
  ) -> (Self, Self::Preprocess) {
    AlgorithmMachine::new(algorithm, keys).seeded_preprocess(cache)
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
