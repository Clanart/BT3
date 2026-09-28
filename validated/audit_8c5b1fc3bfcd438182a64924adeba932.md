### Title
Cached deterministic FROST nonces are reused across signing sessions, enabling private-share recovery - (File: `coordinator/src/tributary/signing_protocol.rs`)

### Summary
`SigningProtocol::preprocess_internal` stores one 32-byte FROST preprocess seed per protocol context and deterministically reconstructs the same nonce pair from it every time that context is used. If the same context is used for multiple signing attempts with different attacker-influenced preprocess sets or messages, the signer reuses the same underlying `(d, e)` nonce pair while publishing new signature shares. Those shares are linear equations in the participant’s secret share and the reused nonce values, allowing an unprivileged participant to recover the victim’s private signing share.

### Finding Description
`CachedPreprocesses` is keyed only by `context`. On first use, `preprocess_internal` generates a random seed, encrypts it with a key derived from the context and node key, stores it in the database, then loads the same seed on every subsequent call and passes it to `AlgorithmSignMachine::from_cache` [1](#0-0) .

`from_cache` calls `seeded_preprocess`, which feeds that cached seed directly into `ChaCha20Rng::from_seed`. The RNG deterministically regenerates both FROST nonces and commitments [2](#0-1) [3](#0-2) . `NonceCommitments::new` creates the nonce pair `(d, e)` from the RNG and secret share [4](#0-3) .

During `sign`, the code derives a public binding factor `rho`, calculates the effective nonce as `d + rho * e`, and returns a Schnorr-style signature share [5](#0-4) . Therefore, for each signing session `j`, the public share has the form:

```text
s_j = nonce_j + c_j * x_i
    = d + rho_j * e + c_j * lambda_i * x_i
```

where `d` and `e` stay fixed for a cached context, while `rho_j` and `c_j` are publicly computable from the submitted preprocesses and message. Three sessions with distinct binding factors/challenges provide three independent equations in the three unknown scalars `x_i`, `d`, and `e`.

This path is reachable through the coordinator’s DKG confirmation API. `generated_key_pair` loads all accumulated confirmation preprocesses and calls `DkgConfirmer::share` [6](#0-5) . Those preprocesses are validator-supplied bytes and are parsed through `read_preprocess` before `share_internal` signs the DKG key-pair message [7](#0-6) . The vulnerable context is only `("DkgConfirmer", attempt)`, with no signing-session nonce or message/commitment binding in the cache key [8](#0-7) .

### Impact Explanation
Recovering a validator’s threshold private share is a complete compromise of that participant’s signing capability. Depending on threshold parameters and attacker-controlled shares, this can contribute to unauthorized threshold signatures and validator impersonation. At minimum, it violates the core FROST requirement that each preprocess/nonce be used exactly once.

The in-code documentation explicitly states that cached preprocess reuse enables recovery of the private key share [9](#0-8) .

### Likelihood Explanation
The flaw is deterministic: every repeated signing operation for the same `context` reconstructs the same seed and nonce material. It does not require malicious validators to control the node, access the database, or obtain the cached seed directly. A participant only needs to cause or observe multiple signing rounds under the same context with varying preprocess/message data and collect the victim’s public signature shares.

The exact exploitation window depends on coordinator retry/attempt handling and whether higher layers permit repeated `share`/`complete` invocations for a context. The cryptographic weakness itself is directly in the in-scope FROST signing path.

### Recommendation
Do not key reusable nonce seeds by protocol context alone. Once a cached preprocess has been used to produce a signing share, it must be atomically consumed and deleted. `share_internal` should not call `preprocess_internal` again from the same context to recreate a previously used machine.

For deterministic reconstruction after restart, persist the entire one-time signing machine state keyed by a unique signing session ID that includes the participant set, preprocess commitments, and message—or reject signing if the same cached seed would be used more than once. The cache-key derivation should also include the exact signing session, not just `("DkgConfirmer", attempt)`.

### Proof of Concept
Let validator `i` reuse cached seed `S` for three signing sessions under the same context.

1. `from_cache` reconstructs nonce pair `(d, e)` from `S`.
2. For session `j`, public preprocesses and message produce public values `rho_j` and challenge `c_j`.
3. The emitted share is:

```text
s_j = d + rho_j * e + c_j * lambda_i * x_i
```

4. Collect `s_1`, `s_2`, and `s_3`. Solve the public linear system:

```text
[1 rho_1 c_1*lambda_i] [d]   [s_1]
[1 rho_2 c_2*lambda_i] [e] = [s_2]
[1 rho_3 c_3*lambda_i] [x_i] [s_3]
```

5. For independent `rho_j`/`c_j`, invert the matrix and recover `x_i`, the participant’s interpolated private key share.

The required deterministic nonce reuse is implemented by `CachedPreprocesses::get`, `AlgorithmSignMachine::from_cache`, and `ChaCha20Rng::from_seed` [10](#0-9) [2](#0-1) .

### Citations

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

**File:** coordinator/src/tributary/signing_protocol.rs (L274-277)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L288-310)
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

**File:** crypto/frost/src/sign.rs (L209-219)
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
```

**File:** crypto/frost/src/sign.rs (L268-274)
```rust
  fn from_cache(
    algorithm: A,
    keys: ThresholdKeys<C>,
    cache: CachedPreprocess,
  ) -> (Self, Self::Preprocess) {
    AlgorithmMachine::new(algorithm, keys).seeded_preprocess(cache)
  }
```

**File:** crypto/frost/src/sign.rs (L361-399)
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

**File:** crypto/frost/src/nonce.rs (L53-72)
```rust
  pub(crate) fn new<R: RngCore + CryptoRng>(
    rng: &mut R,
    secret_share: &Zeroizing<C::F>,
    generators: &[C::G],
  ) -> (Nonce<C>, NonceCommitments<C>) {
    let nonce = Nonce::<C>([
      C::random_nonce(secret_share, &mut *rng),
      C::random_nonce(secret_share, &mut *rng),
    ]);

    let mut commitments = Vec::with_capacity(generators.len());
    for generator in generators {
      commitments.push(GeneratorCommitments([
        *generator * nonce.0[0].deref(),
        *generator * nonce.0[1].deref(),
      ]));
    }

    (nonce, NonceCommitments { generators: commitments })
  }
```

**File:** coordinator/src/tributary/handle.rs (L47-60)
```rust
pub fn generated_key_pair<D: Db>(
  txn: &mut D::Transaction<'_>,
  key: &Zeroizing<<Ristretto as Ciphersuite>::F>,
  spec: &TributarySpec,
  key_pair: &KeyPair,
  attempt: u32,
) -> Result<[u8; 32], Participant> {
  DkgKeyPair::set(txn, spec.genesis(), attempt, key_pair);
  KeyToDkgAttempt::set(txn, key_pair.0 .0, &attempt);
  let preprocesses = ConfirmationNonces::get(txn, spec.genesis(), attempt).unwrap();
  DkgConfirmer::new(key, spec, txn, attempt)
    .expect("claiming to have generated a key pair for an unrecognized attempt")
    .share(preprocesses, key_pair)
}
```
