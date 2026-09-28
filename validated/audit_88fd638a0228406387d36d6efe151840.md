### Title
Cached DKG-confirmation preprocess is never cleared, so a second `generated_key_pair`/`share` under the same attempt reuses FROST nonces and leaks the validator secret share - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The external report's bug class — a cumulative/settled state variable that must be reset after an operation but isn't — maps directly onto `CachedPreprocesses` in `SigningProtocol::preprocess_internal`. The preprocess seed is stored once per `context` ( `(b"DkgConfirmer", attempt)` ), is reused verbatim on every subsequent call for that context, and is never deleted after `share_internal` consumes it. Every call to `DkgConfirmer::share` / `complete` deterministically regenerates the same FROST nonces from that seed and calls `machine.sign(...)`. If `share` is ever invoked twice for the same attempt with a distinct message (distinct `key_pair`, i.e. distinct `set_keys_message`) — which `generated_key_pair` permits because it unconditionally overwrites `DkgKeyPair`/`KeyToDkgAttempt` and re-signs — two distinct signature shares under identical nonces are published to the tributary, enabling third-party recovery of the validator's secret share.

### Finding Description
`SigningProtocol::preprocess_internal` caches a ChaCha20 seed under `CachedPreprocesses::set(self.txn, &self.context, &cache.0)` where `context = (b"DkgConfirmer", self.attempt)` [1](#0-0) . There is no code path that deletes the entry; `share_internal` calls `preprocess_internal` again and re-derives the same machine/nonces via `AlgorithmSignMachine::from_cache` [2](#0-1) . `AlgorithmMachine::seeded_preprocess` derives the nonces deterministically from `ChaCha20Rng::from_seed(*seed.0)`, so the same context always yields the same `(d, e)` nonce pair [3](#0-2) .

The state is "settled" once when `generated_key_pair` calls `.share(preprocesses, key_pair)` and the resulting `[u8; 32]` share is published in a `Transaction::DkgConfirmed` [4](#0-3) . But unlike the fixed-withdrawal functions in the audit report that clear the accumulator, `generated_key_pair` unconditionally executes `DkgKeyPair::set` / `KeyToDkgAttempt::set` and then signs again — there is no guard such as `if DkgKeyPair::get(...).is_some() { return }`. A second invocation for the same attempt with a different `KeyPair` (e.g., a re-emitted `GeneratedKeyPair` from the processor after blame/retry under the same attempt index, or a corrected key pair) produces a second share `s' = d + b·rho' + λ·x·c'` over a different `set_keys_message` while reusing the exact same `d, e`. Both shares are public tributary data.

The file's own safety argument acknowledges this exact failure mode: nonce reuse occurs if "the received nonce commitments (or the message to be signed) would have to be distinct and sign would have to be called again" and enumerates "a logical flaw exists, letting new messages over write prior messages" as a precondition [5](#0-4) . The never-cleared `CachedPreprocesses` entry combined with the unguarded overwrite of `DkgKeyPair` in `generated_key_pair` is precisely such a flaw: the "settled" preprocess state is not cleared after the first `share`, so `sign` can be called again on a distinct message with the same nonce.

### Impact Explanation
Two Schnorr signature shares `s1 = d + b·rho1 + λ·x·c1` and `s2 = d + b·rho2 + λ·x·c2` under identical nonces but different messages/binding factors allow any observer of the tributary (all shares are published on-chain) to solve for the validator's FROST secret share `x` — equivalently here, the validator's MuSig participation share under `musig(...)`, which is derived from the validator's own key `self.key`. This is key share recovery, the exact consequence `CachedPreprocess` documentation warns about: "Reuse will enable third-party recovery of your private key share" [6](#0-5) . Recovery of a validator's key breaks the root-of-trust assumption the entire confirmation protocol is built on.

### Likelihood Explanation
Triggering requires `generated_key_pair` (or equivalently `share`/`complete`) to run twice under one `(b"DkgConfirmer", attempt)` context with a different `key_pair` or preprocess set. Nothing in the coordinator prevents this: `DkgKeyPair::set` overwrites without a check and the cached seed is never consumed. Whether a distinct `KeyPair` can actually be emitted twice for one attempt depends on processor-side behavior in `processor/src/key_gen.rs`, which I could not fully verify within this session; if the DKG output for an attempt is strictly deterministic and emitted exactly once, the trigger reduces to a restart/re-execution edge or a buggy processor — still a real latent violation of the documented single-use invariant, with `complete` itself already re-invoking `share_internal` on the same cached seed [7](#0-6) .

### Recommendation
- After `share_internal` successfully produces a share for a context, delete or mark-consumed the `CachedPreprocesses` entry so a second `sign` under the same context panics instead of reusing nonces.
- In `generated_key_pair`, refuse to overwrite an existing `DkgKeyPair` for `(genesis, attempt)` (return early or assert equality) so a second `share` for a distinct `key_pair` is never reached.
- Implement the noted TODO: verify the on-chain published preprocess matches the presumed cached preprocess before publishing any share.

### Proof of Concept
1. Attempt `a` of a DKG completes; processor emits `GeneratedKeyPair(KP1)`. Coordinator runs `generated_key_pair(txn, key, spec, KP1, a)` → `DkgKeyPair::set(KP1)`; `DkgConfirmer::share(preprocesses, KP1)` loads cached seed `S` under context `(b"DkgConfirmer", a)`, derives nonces `(d, e)`, signs `set_keys_message(set, removed, KP1)` → publishes share `s1` in `DkgConfirmed`.
2. A second `generated_key_pair` call arrives for the same attempt with `KP2 ≠ KP1` (overwrite path is unguarded at handle.rs:54-55). `share` re-loads the same uncleared seed `S`, derives identical `(d, e)`, signs the distinct `set_keys_message(set, removed, KP2)` → publishes `s2`.
3. Any party reading the tributary computes `x = (s1 − s2 − b·(rho1 − rho2)) / (λ·(c1 − c2))` — all values public — recovering the validator's secret share, because the "reward accumulator" analog (the cached preprocess seed) was never cleared after its first consuming `sign`.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L33-41)
```rust

  In order for nonce re-use to occur, the received nonce commitments (or the message to be signed)
  would have to be distinct and sign would have to be called again.

  Before we act on any received messages, they're ordered and finalized by a BFT algorithm. The
  only way to operate on distinct received messages would be if:

  1) A logical flaw exists, letting new messages over write prior messages
  2) A reorganization occurred from chain A to chain B, and with it, different messages
```

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
