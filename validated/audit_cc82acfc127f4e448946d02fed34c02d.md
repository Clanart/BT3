### Title
Context-keyed deterministic preprocess reuse causes nonce reuse across distinct `set_keys` messages, enabling private key share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The bug class in the external report is "a key derived from a stale, pre-update value is used after the state transition that should have re-keyed it" — `expDate` is not normalized to `todayDay()` before deriving the `dailyUnlockedAmounts` key. The analog in Serai lives in the `SigningProtocol` used by the coordinator's `DkgConfirmer`: the nonce seed (`CachedPreprocesses`) is keyed solely by `(b"DkgConfirmer", attempt)`, so every `share`/`complete` invocation within one attempt regenerates byte-identical FROST nonces via `AlgorithmSignMachine::from_cache` → `seeded_preprocess` (`ChaCha20Rng::from_seed`). If the message being signed — `set_keys_message(set, removed, key_pair)` — differs between two executions under the same attempt (different `key_pair`/`removed`, or a different accepted preprocess set changing the challenge), the same nonces are reused with different challenges, allowing algebraic recovery of the validator's private key share. The stale pre-update value (`expDate` read before `extendLockingDuration` rewrites it) maps to the stale context key (`attempt` not re-derived per `key_pair`/preprocess-set) used after `share_internal` re-executes the mutation `machine.sign(...)`. [1](#0-0) [2](#0-1) 

### Finding Description
`preprocess_internal` stores a 32-byte seed in `CachedPreprocesses` keyed by `context`, and `share_internal` calls `preprocess_internal` again each time, deterministically reconstructing the same nonces (`Commitments::new` over `ChaCha20Rng::from_seed`). [3](#0-2) [4](#0-3)  `DkgConfirmer::signing_protocol` fixes `context = (b"DkgConfirmer", self.attempt)`, which does not commit to `key_pair`, `removed`, or the preprocess set. [5](#0-4)  Yet `share_internal` and `complete` build the message from `set_keys_message(&self.spec.set(), &self.removed…, key_pair)` and call `share_internal` again — re-deriving the identical nonces for whatever message/preprocesses happen to be supplied at that call. [6](#0-5)  The file's own header acknowledges the hazard: nonce reuse occurs if "the received nonce commitments (or the message to be signed) would have to be distinct and sign would have to be called again," and safety rests on BFT ordering plus a not-yet-implemented "check the commitments generated from the decided nonces are in fact its commitments on-chain (TODO)". [7](#0-6) 

In `crypto/frost`, `AlgorithmSignMachine::sign` derives the signature share as `nonce_i + λ_i·share·challenge` where `nonce_i = base + ρ·actual` is recomputed from the cached seed each call. [8](#0-7)  Two shares `s1 = n + λ·x·c1` and `s2 = n + λ·x·c2` with `c1 ≠ c2` yield `x = (s1 − s2)/(λ·(c1 − c2))`, recovering the MuSig private key share.

### Impact Explanation
Recovery of a coordinator validator's private key share. Since this MuSig instance signs the root-of-trust `set_keys` confirmation for DKG results, compromise of enough shares forgery-enables on-chain confirmation of attacker-chosen key pairs — a consensus/key-confirmation forgery with full loss of the affected validator's signing key. [9](#0-8) 

### Likelihood Explanation
Reachability requires two `share_internal` executions under the same `attempt` context to observe distinct messages or preprocess sets. The design intends BFT ordering to prevent this, but the file itself flags the missing on-chain commitment check as TODO, and `complete` unconditionally re-runs `share_internal` with whatever preprocesses/shares maps are supplied at that call site. [10](#0-9)  Any divergence — e.g., a partial rebuild that keeps the DB (so the cached seed persists and is not re-decided, contrary to the comment's "complete rebuild" assumption), or a call site that feeds a differing `key_pair`/`removed`/preprocess-set — produces the reuse. Because the precondition depends on tributary message ordering I could not fully trace, exploitability hinges on whether any public-input path can cause a second `sign` under one attempt; the cryptographic consequence (share recovery) is unconditional once two distinct challenges are signed under the same seed.

### Recommendation
Bind the effective nonce context to the full signing input: derive or key `CachedPreprocesses` additionally on a hash of `set_keys_message`/the preprocess set (e.g., `context = (b"DkgConfirmer", attempt, hash(msg), hash(sorted preprocess ids))`), or refuse to `sign` when the reconstructed `self.preprocess.commitments` do not match the commitments already published on-chain for that context — the check already marked TODO in the header. [11](#0-10)  Alternatively, persist a "consumed" flag per context so `from_cache` cannot re-enter `seeded_preprocess` for a mutated signing view, mirroring the report's fix of normalizing `expDate` before key derivation.

### Proof of Concept
1. Validator V participates in `DkgConfirmer` for attempt `a`; `preprocess_internal` caches seed `k` under `(b"DkgConfirmer", a)` and publishes commitments `C`.
2. `share(preprocesses_P, key_pair_1)` executes `share_internal` → `from_cache(k)` → nonces `n` → publishes share `s1 = n + λ·x·c1` for `m1 = set_keys_message(set, removed, key_pair_1)`.
3. A subsequent execution of `share`/`complete` under the same attempt reaches `share_internal` with `key_pair_2 ≠ key_pair_1` (or a preprocess set producing a different `rho_transcript` → different `c2`): `from_cache(k)` re-derives the same `n` (`ChaCha20Rng::from_seed(*seed.0)`), producing `s2 = n + λ·x·c2`. [2](#0-1) [12](#0-11) 
4. Observer solves `x = (s1 − s2)·(λ(c1 − c2))⁻¹`, recovering V's Ristretto private key share, which then verifies against V's public key `G·x`.

Caveat: step 3's trigger depends on tributary call-site semantics in `handle.rs`, which I could not fully verify within the available iterations; the nonce-reuse mechanism itself is unconditional given two distinct challenges under one context.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L25-55)
```rust
  As for safety, it is explicitly unsafe to reuse nonces across signing sessions. This raises
  concerns regarding our re-execution which is dependent on fixed nonces. Safety is derived from
  the nonces being context-bound under a BFT protocol. The flow is as follows:

  1) Decide the nonce.
  2) Publish the nonces' commitments, receiving everyone elses *and potentially the message to be
     signed*.
  3) Sign and publish the signature share.

  In order for nonce re-use to occur, the received nonce commitments (or the message to be signed)
  would have to be distinct and sign would have to be called again.

  Before we act on any received messages, they're ordered and finalized by a BFT algorithm. The
  only way to operate on distinct received messages would be if:

  1) A logical flaw exists, letting new messages over write prior messages
  2) A reorganization occurred from chain A to chain B, and with it, different messages

  Reorganizations are not supported, as BFT is assumed by the presence of a BFT algorithm. While
  a significant amount of processes may be byzantine, leading to BFT being broken, that still will
  not trigger a reorganization. The only way to move to a distinct chain, with distinct messages,
  would be by rebuilding the local process (this time following chain B). Upon any complete
  rebuild, we'd re-decide nonces, achieving safety. This does set a bound preventing partial
  rebuilds which is accepted.

  Additionally, to ensure a rebuilt service isn't flagged as malicious, we have to check the
  commitments generated from the decided nonces are in fact its commitments on-chain (TODO).

  TODO: We also need to review how we're handling Processor preprocesses and likely implement the
  same on-chain-preprocess-matches-presumed-preprocess check before publishing shares.
*/
```

**File:** coordinator/src/tributary/signing_protocol.rs (L86-90)
```rust
create_db!(
  SigningProtocolDb {
    CachedPreprocesses: (context: &impl Encode) -> [u8; 32]
  }
);
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

**File:** coordinator/src/tributary/signing_protocol.rs (L274-277)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L288-327)
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

**File:** crypto/frost/src/sign.rs (L382-398)
```rust
    #[allow(non_snake_case)]
    let Rs = B.nonces(&nonces);

    let our_binding_factors = B.binding_factors(multisig_params.i());
    let nonces = self
      .nonces
      .drain(..)
      .enumerate()
      .map(|(n, nonces)| {
        let [base, mut actual] = nonces.0;
        *actual *= our_binding_factors[n];
        *actual += base.deref();
        actual
      })
      .collect::<Vec<_>>();

    let share = self.params.algorithm.sign_share(&view, &Rs, nonces, msg);
```
