### Title
Cached FROST preprocess seed is never deleted, causing deterministic nonce reuse across signing sessions - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The X.Org bug (CVE-2022-46342) is a use-after-free: a request handler writes to memory after it has been freed. The Serai analog is a **use-after-consumption of a one-shot secret**: `SigningProtocol::preprocess_internal` persists the FROST preprocess seed (`CachedPreprocess`, a ChaCha20 RNG seed) in `CachedPreprocesses` keyed only by `context`, and never deletes it. Every subsequent call to `preprocess_internal` for the same context calls `AlgorithmSignMachine::from_cache`, which deterministically regenerates the *same* nonces via `ChaCha20Rng::from_seed(*seed.0)`. `share_internal` calls `preprocess_internal` on every invocation, and `DkgConfirmer::complete` calls `share_internal` again — so a validator can emit two `SignatureShare`s derived from the same nonces for different preprocess sets/messages, enabling algebraic recovery of its secret share. This is explicitly prohibited by FROST (`crypto/frost/src/sign.rs:85-87`: "A preprocess MUST only be used once. Reuse will enable third-party recovery of your private key share").

### Finding Description
- `preprocess_internal` stores `cache.0` (the seed XORed with an encryption key) under `CachedPreprocesses::set(self.txn, &self.context, ...)` and later reads it back with `CachedPreprocesses::get`; there is no `delete`/`remove` anywhere in the codebase for this key [1](#0-0) .
- `from_cache` → `seeded_preprocess` → `ChaCha20Rng::from_seed(*seed.0)` → `Commitments::new(&mut rng, ...)` makes the scalar nonces `d, e` a pure function of the stored seed [2](#0-1) .
- `share_internal` calls `self.preprocess_internal(participants).0` (line 156), so each call rebuilds an `AlgorithmSignMachine` with identical `self.nonces`. `DkgConfirmer::complete` calls `share_internal` again after `share` already did [3](#0-2) .
- The signature share is `nonce + λ·s·c`-shaped where the nonce contribution is `base + ρ·actual` (lines 388-398 of `sign.rs`). Two shares using the same `(base, actual)` under different binding factors ρ / challenges c yield two linear equations in `(base, actual, s)` — the standard FROST nonce-reuse secret-share recovery.

### Impact Explanation
An attacker who obtains two signature shares produced under the same `context` (e.g., `(b"DkgConfirmer", attempt)` or another `Encode` context) but different preprocess sets/messages recovers the victim validator's MuSig/FROST secret share, collapsing the threshold scheme for that key. Severity: High — key-share recovery is a listed acceptable impact.

### Likelihood Explanation
`complete` unconditionally re-derives the machine via `share_internal` (lines 321-324), so reuse is structural, not accidental. Whether an *unprivileged* party can supply the differing preprocesses depends on whether preprocess messages are treated as public inputs from counterparties; the code path accepts arbitrary `HashMap<Participant, Vec<u8>>` preprocess bytes via `read_preprocess`. Retries after a failed signing set (different participants/preprocess bytes, same attempt context) trigger the differing-ρ case.

### Recommendation
Delete `CachedPreprocesses` for the context the first time a machine is consumed for `sign`, or key the cache by a fresh per-session identifier rather than `(context)`; alternatively regenerate and re-store a new seed after every `from_cache`.

### Proof of Concept
1. Instantiate `SigningProtocol` with fixed `context` C and call `share_internal(participants, preprocesses_A, msg_A)` → emits share `s_1` built from nonces derived from seed S stored under C.
2. Call `share_internal(participants, preprocesses_B, msg_B)` with `preprocesses_B ≠ preprocesses_A` (or a different msg) → `preprocess_internal` reloads S, `ChaCha20Rng::from_seed(S)` regenerates the same `(base, actual)`, producing `s_2` with the same nonce scalars but different ρ/challenge.
3. Solve the two linear share equations for the secret share — the exact attack the `CachedPreprocess` doc-comment warns against [4](#0-3) .

Uncertainty: I could not fully trace every external caller of `DkgConfirmer::share`/`complete` or the coordinator message path that feeds `serialized_preprocesses`; the finding rests on the demonstrated fact that the seed is persistent and `sign` is reachable multiple times per context, which alone violates the documented single-use invariant.

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
