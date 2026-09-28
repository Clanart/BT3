### Title
Cached FROST preprocess is fetched but never consumed, allowing deterministic nonce reuse and secret share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The Derby bug class is a stored value (`rewardPerLockedToken`) that is read to compute a payout but never zeroed, letting an attacker repeatedly claim the same entitlement. Serai has the identical shape in `SigningProtocol::preprocess_internal`: a `CachedPreprocess` seed is stored under `self.context`, read back on every call, and **never deleted**. Because `AlgorithmSignMachine::from_cache` deterministically regenerates the same nonces from that seed (`ChaCha20Rng::from_seed(*seed.0)`), every signing attempt under the same context reuses the same FROST nonces. Reusing a nonce across two different messages lets any observer algebraically recover the signer's secret share.

### Finding Description
`preprocess_internal` in `coordinator/src/tributary/signing_protocol.rs` encrypts and stores a 32-byte preprocess seed keyed by `context` via `CachedPreprocesses::set`, then on every subsequent call reads it back with `CachedPreprocesses::get(self.txn, &self.context).unwrap()` and decrypts it — but nothing ever removes or rotates the entry [1](#0-0) . A grep across the repository confirms `CachedPreprocesses` is only ever `set`/`get` here; it is never deleted.

On the crypto side, `from_cache` → `seeded_preprocess` derives nonces purely from the seed: `ChaCha20Rng::from_seed(*seed.0)` feeds `Commitments::new`, so the same seed always produces identical nonces and identical commitment points [2](#0-1) . `share_internal` calls `preprocess_internal` and then `machine.sign(preprocesses, msg)` [3](#0-2) . If `share_internal` runs twice for the same context — e.g., a retried signing round where the coordinator re-issues the attempt with a different `msg` or a different participant set — the coordinator signs two different messages (or two different binding factors / Lagrange combinations) under the identical nonce.

A Schnorr share is `s = d + b·e + λ_i·x_i·c` (plus binding-factor combinations). Two shares over the same nonce with different challenges or different signing sets yield a linear system solvable for the secret share `x_i`. This is exactly the failure the FROST docs warn about ("Reusing preprocesses would enable a third-party to recover your private key share") [4](#0-3) , and the sign.rs docs likewise state the cached preprocess "MUST only be used once" and "the preprocess must be deleted so it's never reused" [5](#0-4)  — a contract the coordinator code violates because it re-reads the DB entry on every call without consuming it.

### Impact Explanation
Any party who observes two signature shares produced under the same `context` (same cached seed) but different messages or different included-signer sets can recover the coordinator's FROST secret share for that key. Recovery of a threshold share breaks the unforgeability of the affected multisig in combination with other recovered shares, and a single malicious co-signer who collects both shares obtains `x_i` directly. This is key share recovery — the highest-impact primitive-level consequence in scope.

### Likelihood Explanation
The trigger requires two signing attempts under one context with differing effective messages. The context is derived from the signing attempt's identifying data, so distinct attempts normally use distinct contexts; however, any retry path where the same attempt id is re-signed after a failed round (e.g., a malicious participant submitting a garbage preprocess to force `InvalidParticipant` and a re-attempt with a different participant set, changing each signer's binding factors and Lagrange coefficients while the nonce stays fixed) produces two shares over the same nonce. Unlike the Derby report where the call is openly permissionless, here the trigger needs an induced retry, so likelihood is moderate rather than trivial — but the root cause (read-without-consume) is unambiguous.

### Recommendation
Consume the cached preprocess when it is used, mirroring the report's fix pattern:

```diff
 let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap();
+// delete after read so the seed can never be reused
+txn.del(CachedPreprocesses key for self.context);
```

More robustly, rotate the seed: after loading the cached seed, immediately generate and store a fresh random seed under the same context (or a derived `seed' = H(seed || attempt_counter)`) so that any re-entrant/retry call deterministically gets a *different* nonce set instead of an identical one. Additionally, include a monotonically increasing attempt counter in `context` so retries cannot collide.

### Proof of Concept
1. Coordinator enters `share_internal` for context `C`: `preprocess_internal` creates machine from `CachedPreprocesses[C]` seed `s`, signs `msg_1` with nonces `(d, e)` derived from `ChaCha20Rng::from_seed(s)`, emits share `s_1 = f(d, e, λ_1·x, c_1)`.
2. A malicious participant causes the round to abort (e.g., sends an invalid preprocess so `read_preprocess` fails and the coordinator is asked to re-sign `C` with a different signer set / message `msg_2`).
3. `CachedPreprocesses[C]` still holds `s` (never deleted). `preprocess_internal` rebuilds the identical machine — same `d, e` — and signs `msg_2` (or the same message under a different `included` set, changing `λ` and the binding factors), emitting `s_2 = f(d, e, λ_2·x, c_2)`.
4. The attacker solves the two-share linear system for `x`, the coordinator's secret share — directly enabled because the same nonce was reused, which is only possible because the cached seed was read but never consumed [6](#0-5) .

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

**File:** spec/cryptography/FROST.md (L45-62)
```markdown
# Caching

modular-frost supports caching a preprocess. This is done by having all
preprocesses use a seeded RNG. Accordingly, the entire preprocess can be derived
from the RNG seed, making the cache just the seed.

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
