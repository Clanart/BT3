### Title
Deterministic cached FROST preprocess reused across distinct `share` invocations in one context enables nonce reuse and key-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` stores the FROST preprocess seed (`CachedPreprocess`) in the database keyed solely by `context` (e.g. `(b"DkgConfirmer", attempt)`), and `share_internal` reloads that same seed for every share it produces within the context. Because `AlgorithmMachine::seeded_preprocess` derives the nonces purely deterministically from the seed via `ChaCha20Rng::from_seed`, two `share`/`complete` executions inside the same attempt with different attacker-supplied preprocess sets (or different `key_pair` messages) emit signature shares computed with identical `d, e` nonces but different binding factors/challenges — leaking the validator's secret share.

### Finding Description
The seed is created once per context and never invalidated:

```rust
// coordinator/src/tributary/signing_protocol.rs
if CachedPreprocesses::get(self.txn, &self.context).is_none() {
  let (machine, _) = AlgorithmMachine::new(...).preprocess(&mut OsRng);
  let mut cache = machine.cache();
  ... encrypt ...
  CachedPreprocesses::set(self.txn, &self.context, &cache.0);
}
let cached = CachedPreprocesses::get(self.txn, &self.context).unwrap();
let (machine, preprocess) =
  AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached));
```

`share_internal` then calls `self.preprocess_internal(participants).0` (line 156) and signs with it. `from_cache` → `seeded_preprocess` deterministically regenerates the same nonces from the same seed (`crypto/frost/src/sign.rs:121-144`). The `preprocesses` map — entirely composed of attacker/peer-supplied bytes read via `read_preprocess` — determines `included`, the per-participant `rho` binding factors, and the challenge, so each distinct preprocess set produces a distinct share equation `z_i = d + e·rho_i + λ·s·c_i` with the *same* `d, e`. This is the analog of the CVE-2017-18202 race-window reuse: state derived once is consumed multiple times under inputs the caller does not control, violating the one-time-use invariant the nonce derivation assumes.

### Impact Explanation
Each distinct share produced under one context adds one linear equation with the same unknowns `d, e` and `λ·s`. With three or more shares over differing preprocess sets (or differing `key_pair` messages in `DkgConfirmer`), the system is solvable and the signer's private key share `s` is recovered. Compromise of a threshold key share enables signature forgery combined with other recovered shares, and the same pattern applies to any `SigningProtocol` context that calls `share`/`complete` more than once.

### Likelihood Explanation
`DkgConfirmer::share` is invoked per incoming tributary transaction carrying `preprocesses`, and `DkgConfirmer::complete` internally re-runs `share_internal` — multiple calls per `attempt` context are part of normal flow. A peer validator controlling which preprocess sets are delivered (or causing a retried/divergent `key_pair` vote) can force shares over different `included` sets and therefore different `rho` values. No key material or privileged access is required; only peer-submitted preprocess messages.

### Recommendation
Key the cached preprocess by the full signing session (context plus a hash of the preprocess set and message), or delete/rotate the cached seed on first consumption so each `share` consumes a fresh nonce. Alternatively, refuse to produce a second distinct share under a context whose cached preprocess was already consumed.

### Proof of Concept
1. In one `attempt`, deliver preprocess set P1 to a validator's `DkgConfirmer::share`; record share `z1`.
2. Deliver a different valid set P2 (different signer subset or altered peer preprocess bytes); `share_internal` reloads the same seed → identical `d, e`; record `z2`.
3. Repeat with P3; collect `z3`.
4. Solve `z_i = d + e·ρ_i + λ·s·c_i` (ρ_i, λ, c_i publicly computable from `sign.rs` transcript logic) for `s` — the validator's MuSig secret share. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** coordinator/src/tributary/signing_protocol.rs (L150-181)
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
  }
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
