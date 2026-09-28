### Title
Cached FROST preprocess is re-loaded after a failed `sign()` attempt, enabling nonce reuse and secret share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The kernel bug class is an error path that releases a resource without clearing the caller's handle, so the caller uses/releases it again. In `SigningProtocol::preprocess_internal`, the FROST preprocess seed is persisted in the `CachedPreprocesses` DB keyed by `context` and is never invalidated when the downstream `sign()` fails. Because the "resource" here is a one-shot nonce seed, its silent retention on an error path means every retry deterministically regenerates the same nonces — the cryptographic equivalent of a use-after-free. A participant who forces repeated failures obtains multiple signature shares under reused nonces and can solve for the victim's secret share.

### Finding Description
`preprocess_internal` stores a ChaCha20 seed (XOR-encrypted under the key share) in `CachedPreprocesses` the first time a context is seen, and rebuilds the `AlgorithmSignMachine` via `from_cache` on every subsequent call [1](#0-0) . `seeded_preprocess` shows the nonces are fully derived from this seed, so identical seed ⇒ identical `d`/`e` nonces [2](#0-1) .

`share_internal` calls `preprocess_internal`, then `read_preprocess` on each peer's bytes and `machine.sign(...)`, returning `Err(participant)` on `InvalidPreprocess`/`InvalidShare` [3](#0-2) . On that error path nothing deletes or rotates `CachedPreprocesses::get(context)` — the seed is "freed" (consumed by `sign`, which takes `self` and drops the nonces) but the DB handle is not cleared, exactly mirroring the missing `ft->g = NULL`.

The context is attacker-repeatable: `DkgConfirmer` uses `context = (b"DkgConfirmer", attempt)` [4](#0-3) , so a retry of the same attempt regenerates the identical nonce pair. FROST itself documents that preprocess reuse leaks the secret share [5](#0-4) .

### Impact Explanation
A signing set member submits a malformed preprocess (`read_preprocess` fails) or an invalid commitment set, causing `share()`/`share_internal` to error after our machine was rebuilt from the cached seed. When the coordinator retries the attempt with a different preprocess set, our node produces a second share `s₂ = d + e·ρ₂ + λ₂·x·c₂` reusing the same `d, e` while `ρ` (binding factor over all commitments), `λ` (Lagrange coefficient over `included`), and `c` (challenge) change with the attacker-controlled set. Each collected share adds one equation while the unknowns stay `{d, e, x}`; with roughly three shares from forced retries an attacker solves the linear system and recovers our MuSig/FROST secret share — full key share recovery, the exact consequence the upstream fix prevents by NULLing the freed pointer. The same pattern applies to `complete()`, which re-runs `share_internal` and expects prior failure to be impossible [6](#0-5) .

### Likelihood Explanation
Reachable by any participant in a `set_keys`/DKG-confirmation signing round: they control the preprocess bytes fed to `read_preprocess`/`sign` and can force an error after our node consumed the cached seed, then wait for the natural retry of the same `attempt`. Requires no collusion beyond influencing the signing set across retries. Serai's own docs acknowledge seeded-RNG preprocess reuse as a key-recovery failure mode [7](#0-6) .

### Recommendation
Delete or rotate the `CachedPreprocesses` entry whenever a `share_internal`/`complete` path fails after the machine was instantiated, and overwrite it with a fresh random seed before broadcasting the next share — i.e., NULL out the handle on the error path. Alternatively, bind a monotonically increasing counter into `context` for each distinct preprocess set so retries can never reproduce the same nonces.

### Proof of Concept
1. Attacker (Participant `l`) joins a `DkgConfirmer` confirmation for `attempt = k`; honest node caches seed `S` under `(b"DkgConfirmer", k)` and publishes its preprocess.
2. Attacker submits a corrupt preprocess; victim's `share()` returns `Err(l)` after `preprocess_internal` consumed `S`; `CachedPreprocesses[(b"DkgConfirmer", k)]` still holds `S`.
3. Coordinator retries attempt `k` with a different signer set/preprocesses; victim regenerates identical nonces `d, e` from `S` and publishes share `s₂`.
4. Repeat once more; the attacker holds `s₁, s₂, s₃` with known public `ρᵢ, λᵢ, cᵢ` and solves for `(d, e, x)`, recovering the victim's secret share `x`.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L155-180)
```rust
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

**File:** coordinator/src/tributary/signing_protocol.rs (L274-277)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L321-324)
```rust
    let machine = self
      .share_internal(preprocesses, key_pair)
      .expect("trying to complete a machine which failed to preprocess")
      .0;
```

**File:** crypto/frost/src/sign.rs (L127-141)
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
```

**File:** crypto/frost/src/sign.rs (L211-213)
```rust
  /// This cached preprocess MUST only be used once. Reuse of it enables recovery of your private
  /// key share. Third-party recovery of a cached preprocess also enables recovery of your private
  /// key share, so this MUST be treated with the same security as your private key share.
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
