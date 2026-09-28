### Title
Cached FROST preprocess seed is never released and is re-loaded for every share/complete call, enabling nonce reuse across distinct signing sets - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` acquires the signing nonce deterministically from `CachedPreprocesses` — a DB entry keyed by `context` that is written once and **never deleted**. `share_internal` re-executes `preprocess_internal` on every call, rebuilding an `AlgorithmSignMachine` from the same seed. Both `DkgConfirmer::share` and `DkgConfirmer::complete` call `share_internal`, so `AlgorithmSignMachine::sign` is executed two (or more) times under an identical nonce seed within the same `(b"DkgConfirmer", attempt)` context. This is the analog of CVE-2022-48767: a resource acquired for an attempt (the derived sign machine / nonce) is never "put" — it persists and is re-acquired by the next attempt-shaped operation over the same context. [1](#0-0) [2](#0-1) 

### Finding Description
The nonce material lives in `CachedPreprocesses(context) -> [u8; 32]`. `preprocess_internal` sets it only `if ... is_none()` (line 123), decrypts it (lines 137–142), and calls `AlgorithmSignMachine::from_cache` (line 145). There is **no `CachedPreprocesses::del` anywhere** — grep confirms only `set`/`get` usages in this file. The doc comment on `from_cache` states: *"After this, the preprocess must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share"* — a contract `signing_protocol.rs` violates by design. [3](#0-2) 

`DkgConfirmer::complete` calls `self.share_internal(preprocesses, key_pair)` (line 321–324) purely to rebuild the machine — discarding the produced share — and then completes with a separate `shares` map. That is a second full `machine.sign(preprocesses, msg)` execution with the same seed. If the `preprocesses`/`key_pair` seen at `complete` time differ in any way from those used at `share` time (different included signer set, different `KeyPair`, i.e. a different `set_keys_message` msg), the same FROST nonces `d + b·e` are consumed under a different binding factor set / challenge, producing two distinct signature shares over identical nonces. [4](#0-3) [5](#0-4) 

The file's own safety argument (lines 34–48) admits reuse occurs if "the received nonce commitments (or the message to be signed) would have to be distinct and sign would have to be called again" — and `sign` *is* unconditionally called again by `complete`. The code even flags the missing guard: *"we have to check the commitments generated from the decided nonces are in fact its commitments on-chain (TODO)"* (line 50–51).

### Impact Explanation
Two Schnorr/FROST shares `s₁ = n + λ₁·x·c₁` and `s₂ = n + λ₂·x·c₂` over the same nonce `n` with distinct challenges yield the secret share `x` by subtraction: `x = (s₁ − s₂) / (λ₁c₁ − λ₂c₂)`. This is the validator's MuSig root-of-trust key (`self.key`), which underpins all downstream group keys — its recovery compromises the entire validator set's signing authority. This qualifies as key share recovery under the severity rubric.

### Likelihood Explanation
Exploitation requires the coordinator to call `share`/`complete` twice under one `attempt` with differing `preprocesses` or `key_pair` inputs. The inputs are BFT-finalized tributary data, so divergence requires either (a) distinct preprocess sets being presented to `share` vs `complete` for the same attempt — the maps are built per-call from whatever data is supplied, with no check that they equal the set our own published preprocess joined — or (b) a logical flaw/rebuild edge the header comments themselves flag as unhandled (partial rebuilds are "a bound" that "is accepted" but not enforced). Any validator-supplied data item causing a second `share`/`complete` evaluation with a different participant map or `KeyPair` reaches this path; there is no deduplication guard (`AttemptDb`-style gating exists elsewhere in `signer.rs`/`batch_signer.rs` but not here). Reachability is real but gated by BFT-finalized message ordering, supporting Medium rather than High.

### Recommendation
- Delete `CachedPreprocesses` entry after the first successful `machine.sign` for a context (take-and-clear rather than get), so a second `share`/`complete` cannot re-derive the nonce.
- Before `complete` re-executes `share_internal`, verify the supplied `preprocesses`/`key_pair` hash-match those used in the earlier `share` call (the TODO at lines 50–54 already acknowledges this check is required).
- Persist and reuse the signature share itself rather than re-running `sign` to reconstruct the machine.

### Proof of Concept
Sketch (coordinator context, same `attempt`):

```rust
// DkgConfirmer::share is invoked for attempt N with preprocess set P₁, key_pair K₁
confirmer.share(preprocesses_P1, &key_pair_K1); // sign() #1: nonces n, challenge c₁ → s₁

// Later, complete() is invoked for attempt N; the supplied maps differ
// (e.g. a different included signer set P₂ or key_pair K₂ → different msg / binding factors)
confirmer.complete(preprocesses_P2, &key_pair_K2, shares);
// internally calls share_internal → preprocess_internal → from_cache(SAME seed)
// → sign() #2: same nonces n, different challenge c₂ → s₂

// Observer with s₁ and s₂ recovers x:
//   x = (s₁ - s₂) * (λ₁·c₁ - λ₂·c₂)⁻¹  (mod l)
```

Root cause is that `CachedPreprocesses` is `set` once (line 134) and `get` on lines 123/137 but never `del`eted, while `from_cache` explicitly requires deletion after use — the acquired nonce reference is leaked across calls exactly as the leaked `ceph_string` reference in CVE-2022-48767.

*Caveat:* I could not fully trace the `handle.rs` call sites driving `share`/`complete` (tool budget exhausted), so whether differing preprocess sets reach the two calls for one attempt is inferred from the unconditional re-`sign` in `complete` and the code's own TODO noting the absent on-chain commitment check.

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

**File:** crypto/frost/src/sign.rs (L283-313)
```rust
  fn sign(
    mut self,
    mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>>,
    msg: &[u8],
  ) -> Result<(Self::SignatureMachine, SignatureShare<C>), FrostError> {
    let multisig_params = self.params.multisig_params();

    let mut included = Vec::with_capacity(preprocesses.len() + 1);
    included.push(multisig_params.i());
    for l in preprocesses.keys() {
      included.push(*l);
    }
    included.sort_unstable();

    // Included < threshold
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }

    let view = self.params.keys.view(included.clone()).unwrap();
    validate_map(&preprocesses, &included, multisig_params.i())?;
```
