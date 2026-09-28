### Title
Deterministic `CachedPreprocess` re-derivation reuses FROST nonces across `sign()` calls, leaking the secret share - (File: crypto/frost/src/sign.rs)

### Summary
`AlgorithmSignMachine::from_cache` rebuilds a sign machine by feeding the caller-supplied `CachedPreprocess` seed into `ChaCha20Rng`, so the secret nonces `d_i, e_i` generated inside `Commitments::new` are a pure function of `(seed, secret_share, nonce generator list)`. Nothing in `crypto/frost` enforces one-shot use of a seed: `SignMachine::cache()` explicitly hands the seed back to the caller, and `from_cache` can be invoked repeatedly with the same seed while `sign(preprocesses, msg)` accepts fully attacker-controlled preprocess bytes and message bytes. Two `sign()` executions under the same seed with differing preprocess sets or differing `msg` produce two published signature shares over identical nonces, which is the classic FROST nonce-reuse key-recovery condition.

### Finding Description
`seeded_preprocess` derives all nonce material deterministically: [1](#0-0) 

`from_cache` is a public re-entry point that replays the same seed: [2](#0-1) 

The nonces come from `Commitments::new(&mut rng, original_secret_share, ...)` where `rng` is `ChaCha20Rng::from_seed(*seed.0)` — no external entropy. `Curve::random_nonce` itself hashes `seed || secret` deterministically per seed, so identical `ChaCha20Rng` output yields identical `d_i` and `e_i`: [3](#0-2) 

`sign()` then accepts arbitrary `preprocesses` (bytes parsed via `read_preprocess` → `Commitments::read`) and arbitrary `msg`, computes binding factors `rho_l` and the challenge `c = HRAm(R, group_key, msg)`, and emits `s_i = d_i + rho_i·e_i + lambda_i·c·x_i`: [4](#0-3) [5](#0-4) 

With `d_i`, `e_i` fixed, each additional share under a distinct `(preprocesses, msg)` gives one linear equation in the three unknowns `(d_i, e_i, x_i)` with publicly computable `rho_i` (`hash_binding_factor` over the rho transcript), `lambda_i` (Lagrange over the included set), and `c` (public `HRAm`). Three shares with the same cached nonces determine `x_i`.

This mirrors the incident class: an attacker lures the signer into authorizing attacker-shaped "messages" (the analog of the phishing link), and the reused deterministic nonce converts those signatures into full key compromise. The production caller `SigningProtocol` in the coordinator demonstrates exactly this reuse pattern: `preprocess_internal` reloads the same `CachedPreprocesses` entry per context and `share_internal` calls `sign` on attacker-influenced `preprocesses`/`msg` derived from `key_pair`, with `complete()` re-invoking `share_internal` (the file's own header admits safety rests purely on BFT message equality).

### Impact Explanation
Recovery of the validator's/signer’s secret share `x_i` (and thus the MuSig/FROST private key material) from published signature shares — equivalent to the external report's "account compromise" outcome: signatures coerced under attacker-influenced session data collapse into full key theft rather than a single unintended signature.

### Likelihood Explanation
Medium. Exploitation requires the signer to execute `sign()` more than once from the same cached seed with differing preprocess commitments or message — precisely what a re-executing/rebuilt node does when the second execution sees different peer-supplied preprocess bytes or a different `msg`. The preprocess bytes and message are attacker-influenced public inputs reachable via `read_preprocess`/`sign`. The only mitigation is an out-of-band invariant (BFT identical inputs) that `crypto/frost` itself does not enforce; the API exposes `cache()`/`from_cache()` with no nonce-consumption guard.

### Recommendation
- Bind the cached seed to the signing session: derive nonces as `H(seed || hash(commitments) || hash(msg))`-style, or mix the per-session rho transcript into nonce generation, so differing inputs yield differing nonces.
- Alternatively, make `from_cache` one-shot per unique `(preprocesses, msg)` by recording a consumed-session tag alongside the cached seed and refusing a second `sign` under divergent inputs.
- At minimum, document on `CachedPreprocess`/`from_cache` that reuse across distinct sessions is catastrophic and panic on re-sign.

### Proof of Concept
1. Attacker obtains `share_1` for session inputs `(preprocesses_A, msg_A)` where the honest signer rebuilt its machine via `from_cache(seed_S)`.
2. Attacker triggers a second signing (reboot/re-execution or a second `share`/`complete` path) with `(preprocesses_B, msg_B)` — e.g., a different `key_pair` or different commitment set — yielding `share_2` under identical `d_i, e_i`.
3. Repeat once more for `share_3`. Solve the linear system `s_j = d + rho_j·e + lambda_j·c_j·x` (all coefficients public: `rho_j` from `hash_binding_factor` over the rho transcript including `hash_msg(msg)` and `hash_commitments`, `lambda_j` from the included set, `c_j` from `HRAm`) to recover the secret share `x_i`.

### Citations

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

**File:** crypto/frost/src/sign.rs (L264-274)
```rust
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

**File:** crypto/frost/src/sign.rs (L283-371)
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

    {
      // Domain separate FROST
      self.params.algorithm.transcript().domain_separate(b"FROST");
    }

    let nonces = self.params.algorithm.nonces();
    #[allow(non_snake_case)]
    let mut B = BindingFactor(HashMap::<Participant, _>::with_capacity(included.len()));
    {
      // Parse the preprocesses
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
```

**File:** crypto/frost/src/curve/mod.rs (L94-121)
```rust
  fn random_nonce<R: RngCore + CryptoRng>(
    secret: &Zeroizing<Self::F>,
    rng: &mut R,
  ) -> Zeroizing<Self::F> {
    let mut seed = Zeroizing::new(vec![0; 32]);
    rng.fill_bytes(seed.as_mut());

    let mut repr = secret.to_repr();

    // Perform rejection sampling until we reach a non-zero nonce
    // While the IETF spec doesn't explicitly require this, generating a zero nonce will produce
    // commitments which will be rejected for being zero (and if they were used, leak the secret
    // share)
    // Rejection sampling here will prevent an honest participant from ever generating 'malicious'
    // values and ensure safety
    let mut res;
    while {
      seed.extend(repr.as_ref());
      res = Zeroizing::new(<Self as Curve>::hash_to_F(b"nonce", seed.deref()));
      res.ct_eq(&Self::F::ZERO).into()
    } {
      seed = Zeroizing::new(vec![0; 32]);
      rng.fill_bytes(&mut seed);
    }
    repr.as_mut().zeroize();

    res
  }
```

**File:** crypto/frost/src/algorithm.rs (L201-211)
```rust
  fn sign_share(
    &mut self,
    params: &ThresholdView<C>,
    nonce_sums: &[Vec<C::G>],
    mut nonces: Vec<Zeroizing<C::F>>,
    msg: &[u8],
  ) -> C::F {
    let c = H::hram(&nonce_sums[0][0], &params.group_key(), msg);
    self.c = Some(c);
    SchnorrSignature::<C>::sign(params.secret_share(), nonces.swap_remove(0), c).s
  }
```
