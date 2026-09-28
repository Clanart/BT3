### Title
Cached FROST preprocess seed is never invalidated, causing deterministic nonce reuse across signings and secret share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The coordinator deterministically derives FROST nonces from a `CachedPreprocess` seed stored in the database. `preprocess_internal` loads the cached seed every time it is invoked, and `share_internal` calls `preprocess_internal` again internally. The cache is written once (`CachedPreprocesses::set` only when `get(...).is_none()`) and is never cleared or rotated after a signature share is produced. Any repeat signing under the same `context` reuses the identical nonce pair `(d, e)`, violating FROST's single-use nonce requirement and enabling algebraic recovery of the signer's secret share.

### Finding Description
`preprocess_internal` either generates and stores a `CachedPreprocess` seed, or loads the previously stored one for the same `context`, then rebuilds the signing machine via `AlgorithmSignMachine::from_cache` with that seed. [1](#0-0) 

`share_internal` re-enters `preprocess_internal`, meaning every share generation for a given context regenerates the exact same `ChaCha20Rng` seed, the same `Nonce` pair, and therefore the same preprocess commitments. [2](#0-1) 

In `crypto/frost/src/sign.rs`, `from_cache` → `seeded_preprocess` feeds the seed into `ChaCha20Rng::from_seed(*seed.0)`, so identical seeds yield identical `(d, e)` nonce scalars. [3](#0-2) 

Each produced share has the form `s_i = d + e·rho_i + c_i·λ·x` where `x` is the secret share, `rho_i` is the binding factor (bound to group key, `hash_msg(msg)`, and the commitments transcript), and `c_i` is the signature challenge. [4](#0-3) 

The `dkg`/`frost` documentation is explicit that a preprocess "MUST only be used once" and that "reuse will enable third-party recovery of your private key share". [5](#0-4) 

There is no deletion or flag preventing a second use; `CachedPreprocesses::get` returns the same value indefinitely.

### Impact Explanation
An attacker who can cause the same signing context to be executed with different messages (or a different participant set, or even just observe repeated share generation where `rho`/`c` differ) collects linear equations `s_i = d + e·rho_i + c_i·λ·x`. The unknowns are only `(d, e, x)`. With three shares whose `(rho_i, c_i)` pairs are linearly independent, the system is fully determined and the attacker's recovered `x` is the victim validator's threshold secret share. Combined with threshold-1 other shares this yields full group key recovery — the privilege-escalation/sensitive-information analog of CVE-2024-53348 (incorrect access control → obtain sensitive information → escalate privileges): a missing lifecycle/authorization check on a one-time secret converts public signing protocol messages into private key material.

### Likelihood Explanation
The root cause is unconditional: the code path always reuses the stored seed and nothing enforces single use. Exploitation requires the same `context` to be signed more than once with differing `rho`/`c`, e.g., a retried signing operation with different `serialized_preprocesses`/`msg` inputs reaching `share_internal`, or any participant-set change between attempts. Whether a given tributary flow can re-invoke signing under the same context is deployment-dependent, but the cryptographic failure once that happens is deterministic and requires only publicly broadcast signature shares.

### Recommendation
After producing a share from a cached preprocess, delete or tombstone the `CachedPreprocesses` entry (and refuse to sign again for the same context), or bind a monotonically increasing per-use counter / the full `msg`+participant set into the seed preimage so distinct signings cannot regenerate identical nonces. Treat the cache as single-use at the storage layer, matching the "MUST only be used once" contract in `SignMachine::cache`/`from_cache`.

### Proof of Concept
1. Trigger `share_internal` for context `C` producing share `s1` over preprocess set `P1` and message `m1`.
2. Cause a second signing under the same context `C` (e.g., retry with different `serialized_preprocesses` or a different `msg`), producing `s2` with a different binding factor `rho2` and challenge `c2`.
3. Repeat once more for `s3`. Solve the 3×3 linear system `s_i = d + e·rho_i + c_i·λ·x` over the curve's scalar field (all of `rho_i`, `c_i`, `λ` are publicly derivable from broadcast data) to recover the validator's secret share `x`.

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

**File:** crypto/frost/src/sign.rs (L127-143)
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
      preprocess,
    )
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

**File:** crypto/frost/src/sign.rs (L362-398)
```rust
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
