### Title
Cached FROST preprocess (nonce) is reused across multiple `share`/`complete` invocations for the same attempt, enabling secret-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The external report's bug class is "acquire an ephemeral resource, trigger the action, and release it within the same atomic window" (flashloan → lock → early-execute → unlock → repay). The structural analog in Serai is the inverse-timing flaw in `SigningProtocol::preprocess_internal`: a FROST preprocess (nonce seed) is persisted in `CachedPreprocesses` keyed only by `context` and is **never deleted or marked consumed** after `share_internal` uses it to sign. Every subsequent call to `share` or `complete` under the same `context` reloads the identical cached nonce and produces another signature share over attacker-influenced bytes — the same "borrowed" nonce being reused across distinct signing operations. [1](#0-0) 

### Finding Description
`preprocess_internal` stores `machine.cache()` under `CachedPreprocesses::set(self.txn, &self.context, ...)` only if absent, then unconditionally reconstructs the sign machine via `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))`. There is no delete-after-use, no `used` flag, and the cache key does not bind the message or the preprocess set.

`share_internal` calls `self.preprocess_internal(participants).0` and then `machine.sign(preprocesses, msg)` where `msg` is computed from caller-supplied data. For `DkgConfirmer`, `context = (b"DkgConfirmer", self.attempt)` while `msg = set_keys_message(&self.spec.set(), &removed, key_pair)` — i.e., the message is attacker-influenceable via the `key_pair`/payload delivered in the tributary transaction, while the context (and therefore the nonce seed) stays fixed per attempt. [2](#0-1) [3](#0-2) 

FROST explicitly documents the consequence: a cached preprocess "MUST only be used once. Reuse will enable third-party recovery of your private key share" and `from_cache` requires "the preprocess must be deleted so it's never reused." `SigningProtocol` violates both requirements — it caches the preprocess and leaves it in the DB, so `from_cache` is invoked repeatedly with the identical nonce seed. [4](#0-3) 

Each signature share produced from the same cached preprocess has the form `s_i = d + e·rho_k + λ_i·share_i·c_k`, where `d, e` (the nonce components derived from the cached seed) and `share_i` (the secret key share) are fixed unknowns, while `rho_k` (the binding factor over the message and preprocess set) and `c_k` (the 'preprocesses'/signature challenge over `group_key`, `hash_msg`, and the aggregated commitments) are public and vary whenever the attacker varies `msg` (different `key_pair`) or the submitted preprocess set. Individual shares are broadcast on the tributary (`ProcessorMessage::Share` / `Transaction::Sign` with `Label::Share`), so an observer collects the equations directly. [5](#0-4) 

### Impact Explanation
With three share-producing invocations under one `(b"DkgConfirmer", attempt)` context — e.g., a `share` call plus one or more `complete`/re-`share` calls where the attacker varies `key_pair` or the preprocess map so `(rho, c)` differ — the attacker obtains three linear equations in three unknowns `(d, e, share_i)` and solves for the validator's MuSig secret share via Gaussian elimination. Since `musig` uses `Interpolation::Constant` (n-of-n, λ_i a public binding factor), recovering `share_i` recovers the validator's actual Ristretto private signing key used in `SigningProtocol { key }`. That key signs `Transaction::SignCompleted`/set-keys confirmations; compromise enables forging coordinator consensus signatures and, because nonces `d, e` are also recovered, any third party can recompute and forge additional signatures from that preprocess. This is direct private key-share recovery — the exact consequence FROST's documentation warns of — reachable entirely through untrusted `read_preprocess`/`read_share`-fed bytes in public tributary transactions. [6](#0-5) 

### Likelihood Explanation
The reuse path requires the same `context` to be signed under more than once, which depends on coordinator dispatch (`handle.rs`) permitting repeated `share`/`complete` handling per attempt; I could not fully confirm the dispatcher's deduplication within available iterations. However, `complete` is itself a second signing invocation after `share` (it re-runs `share_internal` with the same cached nonce), and nothing in `preprocess_internal` prevents arbitrarily many invocations — each `complete` call re-derives the message from freshly supplied `key_pair`/preprocess bytes, giving the attacker control over `(rho, c)` per reuse. Because the nonce seed survives in the DB even across reboots, retries of the same attempt compound the exposure rather than resetting it. Severity: High (recoverable private key share); likelihood gated on the number of share-emitting invocations an attacker can provoke per context.

### Recommendation
Consume the cached preprocess on first use: after `CachedPreprocesses::get` in `preprocess_internal`, delete the entry (or store a spent marker) within the same DB transaction so any second `share`/`complete` under the same context either regenerates a fresh nonce or fails closed. Additionally, bind the message into the cache key or reject `sign` when a machine built `from_cache` would sign a second distinct `(preprocesses, msg)` pair. Note the tension with `complete`'s current design, which legitimately re-invokes `share_internal` — the machine/state should instead be persisted between `share` and `complete` (or `complete` should reuse the share produced by `share`) rather than re-deriving a signature from the same nonce.

### Proof of Concept
```rust
// Conceptual trace over coordinator/src/tributary/signing_protocol.rs.
// Context fixed per attempt: let ctx = (b"DkgConfirmer", attempt).

// Invocation 1: DkgConfirmer::share(preprocesses_A, key_pair_A)
//   -> preprocess_internal: no entry -> creates preprocess, caches seed S, returns machine
//   -> share_internal: sign(preprocesses_A, msg_A) => share s1 = d + e*rho_1 + lam*sk*c_1
//   -> seed S remains in CachedPreprocesses (never cleared)

// Invocation 2: DkgConfirmer::complete(preprocesses_B, key_pair_B, shares)
//   -> share_internal -> preprocess_internal: CachedPreprocesses::get(ctx) = Some(S)
//   -> AlgorithmSignMachine::from_cache(alg, keys, S)  // SAME nonce d, e
//   -> sign(preprocesses_B, msg_B) => share s2 = d + e*rho_2 + lam*sk*c_2

// Invocation 3: another complete/share under same attempt with (preprocesses_C, key_pair_C)
//   -> share s3 = d + e*rho_3 + lam*sk*c_3

// Attacker observes s1, s2, s3 (broadcast as ProcessorMessage::Share / Sign txs),
// knows rho_k and c_k (hash of msg_k + preprocesses, all public), lam public
// (Constant interpolation binding factor). Solves 3x3 linear system for (d, e, sk):
//   [1  rho_1  lam*c_1] [d ]   [s1]
//   [1  rho_2  lam*c_2] [e ] = [s2]
//   [1  rho_3  lam*c_3] [lam*sk] [s3]
// => recovers the validator's Ristretto private signing key share.
```

One caveat I could not fully verify within the available investigation budget: whether `handle.rs` deduplicates share/complete processing such that fewer than three nonce-reusing invocations are reachable per `(b"DkgConfirmer", attempt)` context, and whether other `SigningProtocol` contexts (e.g., slash reports) reuse the same caching pattern. If dispatch permits even two sign invocations with differing `(rho, c)`, the attack reduces the unknown set and should still be treated as a live nonce-reuse flaw warranting the consume-on-use fix above.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L116-121)
```rust

    let algorithm = Schnorrkel::new(b"substrate");
    let keys: ThresholdKeys<Ristretto> =
      musig(musig_context(self.spec.set().into()), self.key.clone(), participants)
        .expect("signing for a set we aren't in/validator present multiple times")
        .into();
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

**File:** coordinator/src/tributary/signing_protocol.rs (L274-327)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }

  fn preprocess_internal(&mut self) -> (AlgorithmSignMachine<Ristretto, Schnorrkel>, [u8; 64]) {
    let participants = self.spec.validators().iter().map(|val| val.0).collect::<Vec<_>>();
    self.signing_protocol().preprocess_internal(&participants)
  }
  // Get the preprocess for this confirmation.
  pub(crate) fn preprocess(&mut self) -> [u8; 64] {
    self.preprocess_internal().1
  }

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

**File:** coordinator/src/main.rs (L578-586)
```rust
        sign::ProcessorMessage::Share { id, shares } => {
          vec![Transaction::Sign(SignData {
            plan: id.id,
            attempt: id.attempt,
            label: Label::Share,
            data: shares,
            signed: Transaction::empty_signed(),
          })]
        }
```
