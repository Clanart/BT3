### Title
Reusable cached FROST preprocess causes validator private-key recovery if a second confirmation share is issued for a distinct `key_pair` - ([File: coordinator/src/tributary/signing_protocol.rs](coordinator/src/tributary/signing_protocol.rs))

### Summary
`SigningProtocol::preprocess_internal` deterministically rebuilds the FROST nonce material from a DB-cached seed keyed only by `context = (b"DkgConfirmer", attempt)`, and the seed is never rotated or invalidated after a share is produced. `generated_key_pair` writes the reported `key_pair` unconditionally (no already-signed guard) and then calls `DkgConfirmer::share`, which signs `set_keys_message(set, removed, key_pair)` — a message that embeds the caller-supplied `key_pair`. If the function is exercised a second time for the same attempt with a different `key_pair` (the DB row is simply overwritten), the identical nonces sign a distinct message, enabling classic Schnorr nonce-reuse recovery of the validator's Ristretto private key. This mirrors the CVE class: work (the preprocess/nonce) is started once, kept "pending" in `CachedPreprocesses`, and silently reused on a subsequent path that was expected to be a fresh or aborted session.

### Finding Description
In `preprocess_internal`, the nonce seed is loaded-or-created via `CachedPreprocesses::get/set` under `self.context` and then passed to `AlgorithmSignMachine::from_cache`, which feeds it to `Commitments::new` inside `seeded_preprocess` — fully deterministic nonce generation from `(seed, secret_share)` (`crypto/frost/src/sign.rs` `seeded_preprocess`, `from_cache`). Nothing deletes the entry after `share` succeeds or fails, and nothing binds the seed to the message.

`DkgConfirmer::share` → `SigningProtocol::share_internal` signs `msg = set_keys_message(&spec.set(), &removed, key_pair)`. In `handle.rs::generated_key_pair`, `DkgKeyPair::set` overwrites any prior entry and `share` is invoked with the provided `key_pair` — there is no check that a share was already produced for this attempt/context, and no check that the `key_pair` matches a prior signed one. The header commentary in `signing_protocol.rs` itself states the only safety invariant: "In order for nonce re-use to occur, the received nonce commitments (or the message to be signed) would have to be distinct and sign would have to be called again." Here the message is attacker-influenced through `key_pair`, and `sign` is invoked again via an unconditional overwrite, with no on-chain check that "the commitments generated from the decided nonces are in fact its commitments on-chain" (explicitly noted as TODO).

The same reuse also applies to `DkgConfirmer::complete`, which calls `share_internal` a second time under the identical context — safe only because `msg` and preprocesses are assumed identical; any divergence in `key_pair` or `ConfirmationNonces` between the two calls (the code itself flags txn-commit timing races at `handle.rs` DkgConfirmed handling) reuses the nonce on different data.

### Impact Explanation
Two signature shares `s1 = d + ρe - λ·c1·sk` and `s2 = d + ρe - λ·c2·sk` over the same nonce commitments with different challenges `c1 ≠ c2` yield `sk = (s1 - s2) / (λ·(c2 - c1))`. The compromised key is the validator's long-lived Ristretto key — the root of trust used for MuSig DKG-confirmation signing and tributary transaction signing — so recovery enables forging the validator's participation in future confirmations and votes.

### Likelihood Explanation
Triggering requires `generated_key_pair` to run twice for the same attempt with differing `key_pair` values (e.g., a processor re-reporting a different key pair after a faulted/retried DKG share-verification path, or the acknowledged DB-commit timing inconsistency where `DkgKeyPair` and confirmation state diverge). The precondition is a plausible internal race/fault rather than guaranteed attacker control of the bytes, but the cryptographic primitive violated (deterministic nonce reuse across messages) is unconditional once it occurs — consistent with a Critical/High-class latent defect.

### Recommendation
Bind the cached seed to the message or make the share step idempotent and single-use: store the `key_pair` that was signed alongside `CachedPreprocesses` and refuse to sign a different one; set an explicit "share emitted" flag in `generated_key_pair` before signing; or key the cache by `(context, msg_hash)` while enforcing that a context may only ever sign one message. Also implement the noted TODO of verifying on-chain preprocesses match the presumed ones before publishing shares.

### Proof of Concept
1. DKG attempt `a` completes; `ConfirmationNonces` finalized.
2. `generated_key_pair(txn, key, spec, key_pair_A, a)` → `DkgKeyPair::set(A)`; `share` emits `s1` over `m1 = set_keys_message(set, removed, A)` using seed `S = CachedPreprocesses[(b"DkgConfirmer", a)]`.
3. A second report calls `generated_key_pair(txn, key, spec, key_pair_B, a)` with `B ≠ A` → `DkgKeyPair::set` overwrites silently; `share` emits `s2` over `m2 ≠ m1`, regenerating the same machine from the same `S` (same nonces `d, e`, same `R`).
4. Observer of the tributary `DkgConfirmed` shares (or the processor's share outputs) computes `sk = (s1 - s2)/(λ·(c2 - c1))` and recovers the validator's private key. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
