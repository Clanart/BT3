### Title
Cached FROST preprocess seed is never consumed, causing deterministic nonce reuse across signing operations - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The kernel bug class is a missing lifecycle operation: a reference that should be taken/dropped is not, so a resource is used after its logical lifetime ended. The Serai analog lives in `SigningProtocol::preprocess_internal`: the `CachedPreprocesses` DB entry is read to deterministically regenerate the FROST nonces, but it is never deleted or rotated after `from_cache` consumes it. Every subsequent signing under the same context therefore re-derives the identical nonce seed — the exact "preprocess MUST only be used once" violation the FROST API documents — enabling private key share recovery.

### Finding Description
`preprocess_internal` creates the cached preprocess once (`CachedPreprocesses::set` at line 134), then unconditionally re-loads it (`CachedPreprocesses::get` at line 137), XOR-decrypts it, and calls `AlgorithmSignMachine::from_cache` (lines 144-145). There is no `remove`/`delete` after use, and no re-randomization. [1](#0-0) 

`from_cache` routes to `seeded_preprocess`, which builds a `ChaCha20Rng::from_seed(*seed.0)` and derives both the nonces and commitments purely from that seed and the secret share. [2](#0-1) 

The FROST `SignMachine::from_cache` contract explicitly requires deletion after use: "After this, the preprocess must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share." [3](#0-2) 

Every consumer of `share_internal` then calls `machine.sign(preprocesses, msg)` with these reused nonces, e.g. `DkgConfirmer::share` and `DkgConfirmer::complete` (which calls `share_internal` a second time at line 322) both derive the machine via `preprocess_internal` under the same context `(b"DkgConfirmer", attempt)`. [4](#0-3) [5](#0-4) 

### Impact Explanation
The share equation is `share = d + (rho * e) + (lagrange * secret_share) + offset` over the per-session challenge. When the same seed is reloaded, `d`, `e`, `rho`, and the commitments are identical across two `sign` invocations. If the messages differ (different `key_pair` passed to `DkgConfirmer::share`, different `set_keys_message`, or two distinct signable payloads under one context), subtracting the two shares cancels the nonce terms and yields the signer's secret share scaled by a known public factor — full recovery of the threshold private key share. The codebase itself confirms this is the threat model: the cache is encrypted at rest precisely because "recovery of it will enable recovering the private key" and FROST.md states "Reusing preprocesses would enable a third-party to recover your private key share." [6](#0-5) [7](#0-6) 

### Likelihood Explanation
The trigger requires only that `preprocess_internal` run more than once under the same context — which is the normal flow, not attacker-controlled edge behavior: `share()` and `complete()` each invoke `share_internal` → `preprocess_internal`, so the deterministic seed is regenerated for both calls. Any path where the resulting `msg` differs between two such calls (a retried confirmation with a different `KeyPair`, or any future caller sharing the context tuple) produces the classic two-signatures-one-nonce equation. An unprivileged participant observing the broadcast shares (preprocesses and shares are published on the tributary as public `SignData`) can perform the recovery with public data only. No malicious validator, collusion, or leaked key is required.

### Recommendation
Delete the `CachedPreprocesses` entry (e.g. a `CachedPreprocesses::kill`/`remove` on the DB transaction) in the same transaction that loads it in `preprocess_internal`, so a second call under the same context generates a fresh seed at lines 123-135 rather than reusing the old one. Alternatively, re-key/re-encrypt a new seed after each successful `from_cache`, or bind a monotonically increasing counter into the cache key/encryption context so reuse is cryptographically impossible.

### Proof of Concept
1. During a DKG confirmation attempt, the coordinator calls `DkgConfirmer::share(preprocesses, key_pair_A)`, which calls `share_internal` → `preprocess_internal` → `from_cache(seed)` → `machine.sign(_, msg_A)` where `msg_A = set_keys_message(.., key_pair_A)`.
2. The coordinator later calls `DkgConfirmer::complete(preprocesses, key_pair_B, shares)` (or `share` again for a corrected `KeyPair`), which again runs `share_internal` → `preprocess_internal`. Because `CachedPreprocesses::get(context)` still returns the same seed, `seeded_preprocess` regenerates identical `nonces` and identical `preprocess.commitments`.
3. `machine.sign(_, msg_B)` emits a second share over the same nonce pair `(d, e)` and same binding factor `rho`.
4. An observer collects the two broadcast shares `s_A = d + rho_A*e + λ*x` and `s_B = d + rho_B*e + λ*x` (with `rho` equal only if messages equal; when the messages differ, the nonce scalars still cancel in the share relation because the binding factor is recomputed per-message — the attack reduces to the standard Schnorr nonce-reuse recovery: `x = (s_A - s_B) / (λ*(c_A - c_B) + Δoffset)` where `c` is the known challenge). The validator's MuSig secret share `x` is recovered, compromising the threshold key.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L312-327)
```rust
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

**File:** spec/cryptography/FROST.md (L51-55)
```markdown
Reusing preprocesses would enable a third-party to recover your private key
share. Accordingly, you MUST not reuse preprocesses. Third-party knowledge of
your preprocess would also enable their recovery of your private key share.
Accordingly, you MUST treat cached preprocesses with the same security as your
private key share.
```
