### Title
Deterministic nonce reuse in `DkgConfirmer` — cached FROST preprocess is never consumed, allowing secret share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The Serai coordinator's MuSig-based DKG confirmation protocol deterministically regenerates its FROST nonces from a seed stored in `CachedPreprocesses`, keyed only by `("DkgConfirmer", attempt)`. The cached seed is never deleted after use, and `share_internal` re-executes `sign` with the same nonces each time it is invoked. If two distinct `set_keys_message` payloads (i.e., two different `key_pair` values for the same attempt) are ever signed under one context, the resulting two signature shares — both published on the public tributary — form a solvable linear system that yields the validator's private key share.

This maps the CVE-2023-1611 use-after-free class ("use of a resource after its lifetime, yielding stale/duplicated state") onto Serai's actual shape: a cached preprocess that must be treated as single-use is instead a persistent, re-loadable seed.

### Finding Description
`SigningProtocol::preprocess_internal` writes `CachedPreprocesses::set(txn, &context, &cache)` once and then `get`s it on every subsequent call — there is no `remove`/`delete` after the seed is loaded (`coordinator/src/tributary/signing_protocol.rs:123-145`). `share_internal` calls `preprocess_internal` and then `machine.sign(preprocesses, msg)` where `msg = set_keys_message(set, removed, key_pair)` (`signing_protocol.rs:150-181`, `288-302`).

`share` is reached via `generated_key_pair` (`coordinator/src/tributary/handle.rs:47-60`), which unconditionally executes `DkgKeyPair::set(txn, genesis, attempt, key_pair)` — overwriting any prior value with no idempotence check — and then signs. `complete` (`handle.rs:526-535`) calls `share_internal` again with `DkgKeyPair::get`.

The FROST library itself documents the requirement: `from_cache` states "After this, the preprocess must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share" (`crypto/frost/src/sign.rs:216-224`), and `spec/cryptography/FROST.md:51-55` repeats it. The coordinator never deletes the seed, and nothing enforces that `sign` is invoked for only one message per `("DkgConfirmer", attempt)` context. The safety argument in the file header (`signing_protocol.rs:25-51`) relies entirely on BFT guaranteeing identical inputs — there is no cryptographic or DB-level guard against a second `share`/`complete` call carrying a different `key_pair` (e.g., a processor that emits `GeneratedKeyPair` again after a partial rebuild, or a re-emitted message raced with a DB commit — the code itself flags an unhandled commit-timing TODO at `handle.rs:527-528`).

Because `removed` and `attempt` are fixed per context but `key_pair` is attacker/protocol-dependent input to the message, any divergence in `key_pair` across two calls produces two shares `s₁ = k + c₁·λ·x`, `s₂ = k + c₂·λ·x` with the same nonce `k`, directly recovering the MuSig secret share `x`.

### Impact Explanation
Both shares are published in `Transaction::DkgConfirmed` on the public tributary, so any observer (not just validators) can compute `x = (s₁ − s₂) / ((c₁ − c₂)·λ)`. For the MuSig context this recovers the validator's underlying secret key share, enabling forgery of future confirmations/votes under that identity and removal of honest validators via fabricated `RemoveParticipantDueToDkg` flows. This is full key-share recovery — the exact consequence the code comments warn about.

### Likelihood Explanation
The trigger requires `share_internal` to execute twice for one attempt with different `key_pair` values. `generated_key_pair` has no guard preventing a second call, overwrites `DkgKeyPair`, and the processor-side DKG explicitly supports resuming across reboots while signing does not (`processor/src/signer.rs` comments note signing aborts on reboot precisely because "messing up here leaks our secret share"). A partial rebuild, DB commit race, or protocol edge producing a second `GeneratedKeyPair` for the same attempt is sufficient. The code's own safety analysis admits nonce reuse occurs if "a logical flaw exists" or on "partial rebuilds," which it says are prevented by bound rather than enforced. Medium likelihood, high impact.

### Recommendation
- Delete `CachedPreprocesses` (or mark it consumed atomically) the first time `share_internal` signs for a context, and make `preprocess_internal` refuse to rebuild a signing machine for a consumed context.
- Add an idempotence guard in `generated_key_pair`: reject/`assert!` if `DkgKeyPair::get` already returns a different `key_pair` for the same attempt.
- Bind the signed message (or its hash) into the `CachedPreprocesses` context so any divergent message provably cannot reuse the seed.

### Proof of Concept
1. DKG attempt `n` completes; processor sends `GeneratedKeyPair(key_pair_A)`. Coordinator executes `generated_key_pair` → `share(preprocesses, key_pair_A)` → `sign` over `m₁ = set_keys_message(set, removed, A)` using seed-derived nonce `k`. Share `s₁` is published in `DkgConfirmed`.
2. A second `GeneratedKeyPair(key_pair_B)` for the same attempt reaches the coordinator (re-emitted processor message / partial rebuild as contemplated in the header comment). `DkgKeyPair::set` silently overwrites with `B`; `share` signs `m₂ = set_keys_message(set, removed, B)` — `preprocess_internal` reloads the same `CachedPreprocesses` seed → identical `k`. `s₂` is published.
3. Observer computes `x = (s₁ − s₂)·((c₁ − c₂)·λ)⁻¹` mod the group order, recovering the validator's secret share.

The same reuse window exists in `DkgConfirmer::complete`, which invokes `share_internal` a second time whenever it runs — safe only because `DkgKeyPair::get` currently returns the identical pair, an invariant nothing in the code enforces. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L123-148)
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
