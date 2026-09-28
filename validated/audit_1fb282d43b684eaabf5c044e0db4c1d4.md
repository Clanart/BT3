### Title
Deterministic nonce reuse from `CachedPreprocess` allows secret share recovery when a signer is re-triggered for the same context - ([File: coordinator/src/tributary/signing_protocol.rs])

### Summary
Serai's signing protocol regenerates the FROST preprocess deterministically from a DB-cached 32-byte seed (`CachedPreprocess`) every time `share_internal` is invoked for a given context. Because the nonces `(d, e)` are fully determined by this seed, an unprivileged co-signer who can cause the same signing context to be evaluated multiple times with attacker-influenced preprocess sets/messages obtains multiple signature shares `s_j = d + e·rho_j + c_j·λ·x_i` over the *same* nonces. Three such shares form a solvable linear system, recovering the victim's secret share `x_i` — a ROS/parallel-session style nonce-reuse key-share recovery.

### Finding Description
`SigningProtocol::share_internal` unconditionally calls `preprocess_internal`, which rebuilds the `AlgorithmSignMachine` via `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))` using a seed stored in `CachedPreprocesses` and XOR-encrypted with a context-derived key [1](#0-0) . The code explicitly acknowledges the danger: "Encrypt the cached preprocess as recovery of it will enable recovering the private key" [2](#0-1) .

Per the project's own spec, "the entire preprocess can be derived from the RNG seed" and "Reusing preprocesses would enable a third-party to recover your private key share" [3](#0-2) . `from_cache` re-derives identical nonces for the same seed [4](#0-3) , and `sign` computes the share as `d + e·rho + c·λx` where `rho` is bound to the message and the (partially attacker-controlled) preprocess set via `hash_msg`/`hash_commitments` in the rho transcript [5](#0-4) , while the nonce combination `base + rho·actual` reuses the same underlying `d, e` [6](#0-5) .

An unprivileged participant controls their own preprocess bytes fed to `read_preprocess` [7](#0-6) . Each distinct preprocess set (or message) submitted under the same context yields a new share equation with fresh `rho_j`, `c_j` but identical `d`, `e`, `x_i`. With three shares the linear system over the three unknowns `d`, `e`, `λx_i` is solvable, disclosing the signer's interpolated secret share.

### Impact Explanation
Recovery of a victim validator's FROST secret share. Combined with the attacker's own shares, this reduces the effective threshold of the multisig and can enable forging shares for signing sets the victim was part of — direct key-share recovery, which the codebase itself classifies as private-key compromise [2](#0-1) .

### Likelihood Explanation
Requires inducing multiple `share_internal` evaluations under one context with differing preprocess inputs — e.g., re-delivered/retried signing messages, or preprocess sets manipulated by a malicious co-signer — while the cached seed persists across calls and reboots (it is DB-backed) [8](#0-7) . Each retry deterministically reuses the nonces rather than resampling them.

### Recommendation
Bind the cached preprocess to the exact message and full preprocess set being signed (e.g., key `CachedPreprocesses` by `H(context || msg || sorted preprocess hashes)`), or refuse to produce a second share for a context once a distinct preprocess set/message is observed. Alternatively, re-randomize nonces per `sign` invocation so a cached seed never fixes `(d, e)` across independent signing evaluations.

### Proof of Concept
1. Attacker is participant `j` in a `t`-of-`n` MuSig/FROST set with honest signer `i`.
2. Trigger signing for context `C` with preprocess set `P1` → victim emits `s1 = d + e·rho1 + c1·λx_i`.
3. Submit altered own-preprocess bytes (via `read_preprocess`) for the same context `C`, producing `P2` → victim emits `s2 = d + e·rho2 + c2·λx_i`.
4. Repeat with `P3` → `s3`.
5. Solve the 3×3 linear system in `(d, e, λx_i)` over `C::F` — all `rho_j`, `c_j` are publicly computable — yielding the victim's secret share.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L104-106)
```rust
    // Encrypt the cached preprocess as recovery of it will enable recovering the private key
    // While the DB isn't expected to be arbitrarily readable, it isn't a proper secret store and
    // shouldn't be trusted as one
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

**File:** coordinator/src/tributary/signing_protocol.rs (L161-168)
```rust
    for participant in participants {
      preprocesses.insert(
        participant,
        machine
          .read_preprocess(&mut serialized_preprocesses.remove(&participant).unwrap().as_slice())
          .map_err(|_| participant)?,
      );
    }
```

**File:** spec/cryptography/FROST.md (L47-53)
```markdown
modular-frost supports caching a preprocess. This is done by having all
preprocesses use a seeded RNG. Accordingly, the entire preprocess can be derived
from the RNG seed, making the cache just the seed.

Reusing preprocesses would enable a third-party to recover your private key
share. Accordingly, you MUST not reuse preprocesses. Third-party knowledge of
your preprocess would also enable their recovery of your private key share.
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

**File:** crypto/frost/src/sign.rs (L385-398)
```rust
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
