### Title
Cached FROST preprocess is never deleted after use, enabling nonce reuse and private key share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` stores a deterministic preprocess seed in `CachedPreprocesses` keyed by `context`, loads it via `AlgorithmSignMachine::from_cache` on every call, and never deletes it. Because both `share()` and `complete()` route through `share_internal`, the same seed — and therefore the same FROST nonces — is consumed more than once for a single signing context. This mirrors the kernel bug class: state set up once is never torn down, so a one-time resource is silently reused.

### Finding Description
In `preprocess_internal`, if no cache exists for the context a fresh `CachedPreprocess` seed is generated and persisted; on every subsequent call the same seed is decrypted and passed to `from_cache`, which deterministically regenerates the identical nonces via `ChaCha20Rng::from_seed` in `seeded_preprocess` [1](#0-0) [2](#0-1) . There is no `CachedPreprocesses::del`/remove anywhere — the entry survives after the signature share is produced, so any later `share_internal` for the same `context` reuses the exact nonce pair [3](#0-2) . `DkgConfirmer::share` and `DkgConfirmer::complete` both call `share_internal`, and `complete` additionally re-executes the full `share_internal` path, meaning two shares under one seed are emitted in normal flow [4](#0-3) . The crate itself documents that reuse "will enable third-party recovery of your private key share" and that after `from_cache` "the preprocess must be deleted so it's never reused" — a requirement the caller violates [5](#0-4) [6](#0-5) .

### Impact Explanation
Each call to `share_internal` produces a signature share `s_i = d_i + e_i·rho_i·lambda_i + c·share_i` (FROST form). Two shares built from the same seed reuse `(d_i, e_i)`. If anything differs between the two invocations — the preprocess map (different participant set → different binding factor `rho` and Lagrange `lambda`), or the message (`key_pair` in `set_keys_message` → different challenge `c`) — an observer of both shares obtains two linear equations that yield the signer's secret key share. Compromise of one MuSig participant's share directly degrades the threshold security of `set_keys` confirmations; combined with threshold-many such recoveries it yields full group key extraction. Worst case, even identical-parameter reuse is ROS-style exploitable: the attacker collects a share in `share`, then triggers `complete` with a different `preprocesses`/`shares` set, getting a second share under the same commitments.

### Likelihood Explanation
An unprivileged participant in the DKG-confirmation MuSig set can supply `preprocesses` to `share()` and later `shares`/`preprocesses` to `complete()` — both are public round messages they cause to be signed. The `key_pair` and `preprocesses`/`shares` maps are external inputs; the attacker controls their contents across the two calls. Because the cached seed is keyed only by `(b"DkgConfirmer", attempt)` and is never invalidated, the second invocation deterministically reuses the nonces — no race, reboot, or privilege is required, only two protocol-round messages the attacker already sends.

### Recommendation
Delete `CachedPreprocesses` for the context atomically when the seed is loaded in `preprocess_internal` (i.e., a `take` semantics rather than `get`), so `share()` marks the context spent and `complete()` either reuses the already-produced `AlgorithmSignatureMachine` (returned by `share_internal` but currently discarded) or fails cleanly instead of re-deriving the same nonces. At minimum, `complete` should be refactored to accept the signature machine produced by `share` rather than re-running `share_internal`.

### Proof of Concept
1. Honest validator V participates in DKG confirmation attempt `a`; `DkgConfirmer::new` creates context `(b"DkgConfirmer", a)`.
2. Attacker A (another participant) sends its preprocess; V's `share(preprocesses, key_pair_1)` runs `share_internal` → `preprocess_internal` loads seed `s` from `CachedPreprocesses` (created on first use), signs, returns share `σ_1 = d + e·rho_1·lambda + c_1·x_V`. The DB entry for `(b"DkgConfirmer", a)` remains.
3. A later calls `complete(preprocesses', key_pair_2, shares)` with a different key pair or different participant set. `complete` re-runs `share_internal` → `preprocess_internal` loads the same seed `s` → same `d, e` → share `σ_2 = d + e·rho_2·lambda' + c_2·x_V` with `rho_2 ≠ rho_1` (different preprocesses) or `c_2 ≠ c_1` (different message).
4. With `σ_1`, `σ_2`, known public values, A solves for the unknown scalar combination and then `x_V` — recovering V's private key share, exactly the consequence the `CachedPreprocess`/`from_cache` documentation warns of.

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

**File:** crypto/frost/src/sign.rs (L83-92)
```rust
/// A cached preprocess.
///
/// A preprocess MUST only be used once. Reuse will enable third-party recovery of your private
/// key share. Additionally, this MUST be handled with the same security as your private key share,
/// as knowledge of it also enables recovery.
// Directly exposes the [u8; 32] member to void needing to route through std::io interfaces.
// Still uses Zeroizing internally so when users grab it, they have a higher likelihood of
// appreciating how to handle it and don't immediately start copying it just by grabbing it.
#[derive(Zeroize)]
pub struct CachedPreprocess(pub Zeroizing<[u8; 32]>);
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
