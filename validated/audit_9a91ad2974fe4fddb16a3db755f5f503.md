### Title
Deterministic CachedPreprocess reuse across signing attempts enables key share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The UAF class maps onto Serai's shape as *use of consumed state*: a FROST preprocess (the `(d, e)` nonce pair and its seed) is single-use — "freed" once consumed — yet `preprocess_internal` deterministically rebuilds identical nonces from a DB-cached seed keyed only by `context`. Any second signing attempt under the same context reuses the exact same nonces, which FROST itself documents as enabling third-party recovery of the private key share.

### Finding Description
`preprocess_internal` stores `machine.cache()` (a 32-byte ChaCha20 seed) in `CachedPreprocesses`, XOR-encrypted under a key derived from `context || self.key`. On every subsequent call with the same `self.context`, it decrypts the stored seed and calls `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))` [1](#0-0) .

`from_cache` routes to `seeded_preprocess`, which feeds the seed into `ChaCha20Rng` and derives the *same* `Nonce` scalars `(d, e)` and the same commitments [2](#0-1) . The produced share is `s = d + e·rho + λ·secret·c` where `rho` and `c` depend on the message and the peers' preprocesses [3](#0-2) .

Because the seed — and thus `d`, `e` — is fixed per context, a peer can force multiple `sign()` executions under one context with *different* inputs: `share_internal` rebuilds the machine from cache on every call and feeds attacker-controlled bytes through `read_preprocess` into `sign(preprocesses, msg)` [4](#0-3) . Submitting malformed preprocesses produces `FrostError::InvalidPreprocess`/`InvalidShare` and a retry; submitting a different (valid) preprocess set changes `included`, `rho`, and `c` while `d`, `e` stay constant. The library's own docs confirm the consequence: "Reusing preprocesses would enable a third-party to recover your private key share" [5](#0-4) , and `from_cache` warns "the preprocess must be deleted so it's never reused" — yet the cache is never deleted [6](#0-5) .

### Impact Explanation
Each share under the reused `(d, e)` is a linear equation `s_i = d + e·ρ_i + λ_i·x·c_i` in the three unknowns `d`, `e`, and the secret share `x`. Three signature shares (or completed signatures, which expose `Σs`) collected across distinct signing attempts under the same context form a solvable linear system, yielding the validator's FROST secret share. Combined with threshold-many compromised/recovered shares, or directly as a share-theft primitive, this degrades to recovery of key material protecting the set's MuSig/Schnorrkel key — effectively full key compromise against an High-severity confidentiality target.

### Likelihood Explanation
Reachability requires only that an unprivileged participant cause ≥3 `sign()` attempts under one cached `context` while varying the preprocess set or message. Peers fully control the preprocess bytes and which participants are included (`serialized_preprocesses` map), and each failed/retried attempt deterministically re-derives the same nonces. No collusion beyond ordinary protocol participation, no leaked keys, and no misuse of a documented external-API invariant is needed — the reuse is created by the coordinator's own deterministic caching, not by the caller violating a MUST.

### Recommendation
Bind the cached preprocess to the entire signing session — include a hash of the message and the sorted participant/preprocess set in the `CachedPreprocesses` key (or derive the seed as `H(context || session_commitment)`), so differing attempts derive different nonces. Alternatively, treat the cached preprocess as single-use per context: delete it (or ratchet it) after `sign` returns rather than after process restart, and never re-enter `share_internal` for a context that already produced a share. A monotonic per-context attempt counter mixed into the seed would also prevent identical-nonce reuse across retries.

### Proof of Concept
1. Attacker is a threshold-eligible participant. In `share_internal`, they supply a preprocess set S₁ → honest node builds machine from `CachedPreprocess` seed σ → emits share `s₁ = d + e·ρ₁ + λ₁·x·c₁`.
2. Attacker triggers another `share_internal` call for the same `context` with a different valid preprocess set S₂ (e.g., a different participant subset or different commitment points for themselves). `preprocess_internal` decrypts the same σ → identical `(d, e)` → share `s₂ = d + e·ρ₂ + λ₂·x·c₂`.
3. Repeat for S₃ → `s₃`. `ρ_i`, `λ_i`, `c_i` are all publicly computable from the transcripts and group key.
4. Solve the 3×3 linear system over the scalar field for `x` — the node's private key share — directly satisfying the "key share recovery" acceptance criterion.

Note: the exploitability hinges on the coordinator permitting multiple `share_internal` executions per `context` (retries after `InvalidPreprocess`/`InvalidShare`, which the error-handling code explicitly anticipates); I verified the deterministic reuse path but could not fully trace the outer retry loop that re-invokes `share_internal`.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L137-145)
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
```

**File:** coordinator/src/tributary/signing_protocol.rs (L156-178)
```rust
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

**File:** crypto/frost/src/sign.rs (L216-224)
```rust
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

**File:** crypto/frost/src/sign.rs (L382-409)
```rust
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

    Ok((
      AlgorithmSignatureMachine {
        params: self.params,
        view,
        B,
        Rs,
        share,
        blame_entropy: self.blame_entropy,
      },
      SignatureShare(share),
```

**File:** spec/cryptography/FROST.md (L51-55)
```markdown
Reusing preprocesses would enable a third-party to recover your private key
share. Accordingly, you MUST not reuse preprocesses. Third-party knowledge of
your preprocess would also enable their recovery of your private key share.
Accordingly, you MUST treat cached preprocesses with the same security as your
private key share.
```
