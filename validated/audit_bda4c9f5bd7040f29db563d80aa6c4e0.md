### Title
Deterministic cached preprocess is reloaded for every `share`/`complete` call under the same context, enabling nonce reuse and private key share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
Analogous to vault shares left inside `IchiVaultSpell` that anyone can re-sweep via a public call, the coordinator leaves a deterministic preprocess seed in the database (`CachedPreprocesses`) keyed by `context`. Every call to `share()` and `complete()` under the same `(b"DkgConfirmer", attempt)` context reloads the *same* seed, regenerating identical FROST nonces, while the participant-supplied preprocess set — which determines the binding factors and challenge — is provided fresh each time from untrusted on-chain data.

### Finding Description
`SigningProtocol::preprocess_internal` stores a 32-byte seed the first time a context is seen and reuses it on every subsequent call:

- Seed is generated once, then reloaded and decrypted for all later calls under the same context. [1](#0-0) 
- `share_internal` always calls `preprocess_internal`, rebuilding an `AlgorithmSignMachine` from the cached seed via `from_cache` → `seeded_preprocess`, which derives nonces deterministically with `ChaCha20Rng::from_seed(*seed.0)`. [2](#0-1) [3](#0-2) 
- `DkgConfirmer::share` signs the `set_keys_message`, and `DkgConfirmer::complete` calls `share_internal` *again* with whatever preprocess map was supplied in that invocation — i.e., the same deterministic nonces are committed to a second signing run. [4](#0-3) 
- The context is only `("DkgConfirmer", attempt)`, so all share-production attempts within one DKG attempt reuse one seed. [5](#0-4) 

The preprocesses consumed by `share_internal` are parsed from bytes supplied by participants (`read_preprocess` on `serialized_preprocesses`), and the per-participant binding factor `rho` in FROST is computed over each participant's preprocess commitments and the signing set. Two invocations with different valid preprocess sets therefore produce shares `s_i = d + e·ρ_i + c_i·λ·x` that share the same unknowns `(d, e, x)`. The library explicitly warns this is fatal: "Reusing preprocesses would enable a third-party to recover your private key share." [6](#0-5) 

### Impact Explanation
Each additional signed share over the same nonce seed contributes one linear equation in the three unknowns `(d, e, x)` (nonce pair plus the MuSig secret share). After three distinct shares — obtainable via `share()`, the re-sign inside `complete()`, and any retry/re-submission under the same attempt context — an attacker solves for `x`, the validator's MuSig secret share for the confirmation key. Recovery of this key lets the attacker forge that validator's DKG-confirmation `set_keys` signatures and any other signatures produced through the same `SigningProtocol`, violating the threshold assumption.

### Likelihood Explanation
A tributary participant can submit distinct serialized preprocess sets (each individually valid, passing `read_preprocess` and the empty-trailing-bytes check) in separate transactions — e.g., the set used at `share()` time versus the set embedded in the shares submission consumed by `complete()`, or repeated share submissions within one attempt. Each triggers a fresh `sign()` reusing the same seed. No validator collusion or privileged access is required; the inputs are ordinary protocol messages. Determinism is guaranteed by `ChaCha20Rng::from_seed`.

### Recommendation
Delete or rotate `CachedPreprocesses` after a share is produced, or bind the cached seed to the exact preprocess set (e.g., store a hash of the canonical preprocess map alongside the seed and refuse to sign if it differs). At minimum, `complete()` should not re-derive nonces from the same seed for a different preprocess set — it should reuse the `AlgorithmSignatureMachine` produced by the original `sign()`. Ensure one seed produces at most one share per context.

### Proof of Concept
1. Validator V's coordinator calls `DkgConfirmer::preprocess()` under context `("DkgConfirmer", a)`; a seed `S` is stored.
2. Attacker participant submits preprocess set `P1`; `share()` derives nonces `(d, e)` from `S` and emits `s1 = d + e·ρ1 + c1·λ·x`.
3. Attacker submits a shares-completion transaction carrying a different valid preprocess set `P2` (e.g., reordered participants or different addendum bytes). `complete()` → `share_internal` → same `S` → same `(d, e)`, new `ρ2`, `c2`, emitting `s2`.
4. A further re-submission yields `s3`. From `s1, s2, s3` and the public `ρ_i, c_i, λ`, solve the 3×3 linear system for `x`, the validator's secret share.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L150-157)
```rust
  fn share_internal(
    &mut self,
    participants: &[<Ristretto as Ciphersuite>::G],
    mut serialized_preprocesses: HashMap<Participant, Vec<u8>>,
    msg: &[u8],
  ) -> Result<(AlgorithmSignatureMachine<Ristretto, Schnorrkel>, [u8; 32]), Participant> {
    let machine = self.preprocess_internal(participants).0;

```

**File:** coordinator/src/tributary/signing_protocol.rs (L274-277)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }
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

**File:** crypto/frost/src/sign.rs (L121-143)
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
```

**File:** spec/cryptography/FROST.md (L51-55)
```markdown
Reusing preprocesses would enable a third-party to recover your private key
share. Accordingly, you MUST not reuse preprocesses. Third-party knowledge of
your preprocess would also enable their recovery of your private key share.
Accordingly, you MUST treat cached preprocesses with the same security as your
private key share.
```
