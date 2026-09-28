### Title
Deterministic CachedPreprocess reuse lets an unprivileged co-signer recover the private key share via ROS-style parallel signing - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The Squirrelly bug class — attacker-controlled data silently steering internal "configuration" state — maps onto Serai's FROST signing stack: the signing set (which preprocesses/participants are included) is fully attacker-controlled message data, while the nonce configuration is fixed per `context` by a deterministic cached seed. `share_internal` rebuilds the sign machine from `CachedPreprocesses` every time it is invoked, regenerating the *same* nonces for the same context. Because the attacker chooses which preprocesses/participants go into `sign` (the "options" here), each invocation is effectively a parallel session sharing one nonce pair — the precondition for ROS-style nonce-reuse key recovery.

### Finding Description
`preprocess_internal` caches a preprocess seed in the DB keyed by `self.context` and, on every call — including the call made inside `share_internal` — decrypts that same seed and calls `AlgorithmSignMachine::from_cache`. `from_cache` routes to `seeded_preprocess`, which derives all nonce scalars deterministically via `ChaCha20Rng::from_seed(*seed.0)`. [1](#0-0) [2](#0-1) 

`share_internal` then calls `machine.sign(preprocesses, msg)` where `preprocesses` comes straight from `serialized_preprocesses` — coordinator/peer-supplied bytes read via `read_preprocess`, with the set of participants determined by `preprocesses.keys()`. [3](#0-2)  Inside `sign`, the per-participant binding factor ρ is computed over a transcript that commits to the *entire preprocess set*, so a different participant/preprocess selection yields a different ρ while the underlying nonces `(d, e)` remain identical. [4](#0-3) [5](#0-4) 

Each produced share is `z_j = d + e·ρ_j + c_j·s_i` (Schnorr `sign_share` over the combined nonce `d + e·ρ`). [6](#0-5)  Nothing in `share_internal` records that a share was already emitted for this context or enforces single-use: `preprocess_internal` happily regenerates the machine on every invocation (the only cache check is `is_none()` before *creating* the seed). The spec itself states that reusing a preprocess "would enable a third-party to recover your private key share" — this code path reuses the same underlying nonces whenever it runs more than once for a context. [7](#0-6) 

### Impact Explanation
If `share_internal` is invoked more than once for the same `context` (retry after a fault, a re-issued coordinator message, a competing signing-set proposal — all of which alter only the attacker-influenced `serialized_preprocesses`/participant set), a malicious co-signer obtains multiple shares of the form `z_j = d + e·ρ_j + c_j·s_i` with `d, e, s_i` unknown and `ρ_j, c_j` computable by the attacker. Three such shares give three linear equations in three unknowns, solvable directly for the honest participant's secret share `s_i` — or fewer if combined with the ROS attack's linear-algebra reduction across many sessions. Compromise of a private key share defeats the threshold assumption and, combined with threshold-many compromised shares, enables forging signatures / stealing funds under the group key.

### Likelihood Explanation
The reachability conditions are: (a) `share_internal` runs ≥3 times for the same `context` — plausible whenever the protocol retries signing for a context (e.g., after an `InvalidPreprocess`/`InvalidShare` fault returns a blameable error at line 177, a natural retry path exists since no "already shared" flag is set); (b) an unprivileged participant supplies different preprocess sets across invocations — fully within the attacker's control since `serialized_preprocesses` are inbound protocol messages. This does not require validator compromise beyond the ordinary threshold threat model of one malicious participant; it only requires the honest code to re-answer a share request, which the caching design explicitly supports (`from_cache` exists precisely to reconstruct the machine on demand). Given a deterministic seed per context, reuse is not probabilistic — it is guaranteed once the code path is re-entered.

### Recommendation
- Make share emission single-use per context: persist a "share emitted" flag alongside `CachedPreprocesses` (or delete the cached seed) the first time `share_internal` produces a share, and refuse subsequent sign invocations for that context.
- Mix per-invocation entropy into `from_cache`/`seeded_preprocess` (e.g., hash `seed || included || msg` through `Curve::random_nonce`-style derivation) so nonces are bound to the signing set rather than to the context alone — though single-use enforcement is the primary fix per the FROST spec's MUST.
- Consider binding the `msg` and `included` set into the cached-context key so a retry necessarily derives a fresh preprocess.

### Proof of Concept
1. Attacker (a threshold participant) triggers a signing session for context `C` and collects the honest validator's share `z_1 = d + e·ρ_1 + c_1·s_i` for signing set `S_1`.
2. Attacker induces a retry for the same `C` (e.g., submits a different `serialized_preprocesses` map `S_2` so the first attempt faults/aborts and `share_internal` is called again). `preprocess_internal` decrypts the same cached seed and regenerates identical `d, e`; the different preprocess set yields `ρ_2 ≠ ρ_1`. Collect `z_2`.
3. Repeat once more for `z_3`. Solve the 3×3 linear system over the scalar field for `(d, e, s_i)` — recovering the honest participant's FROST private key share from public protocol messages only.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L137-147)
```rust
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

**File:** crypto/frost/src/sign.rs (L290-312)
```rust
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
```

**File:** crypto/frost/src/sign.rs (L361-371)
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

**File:** spec/cryptography/FROST.md (L51-62)
```markdown
Reusing preprocesses would enable a third-party to recover your private key
share. Accordingly, you MUST not reuse preprocesses. Third-party knowledge of
your preprocess would also enable their recovery of your private key share.
Accordingly, you MUST treat cached preprocesses with the same security as your
private key share.

Since a reused seed will lead to a reused preprocess, seeded RNGs are generally
frowned upon when doing multisignature operations. This isn't an issue as each
new preprocess obtains a fresh seed from the specified RNG. Assuming the
provided RNG isn't generating the same seed multiple times, the only way for
this seeded RNG to fail is if a preprocess is loaded multiple times, which was
already a failure point.
```
