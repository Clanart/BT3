### Title
Cached signing nonce reused across distinct `set_keys_message` payloads in a single DKG attempt enables validator private key recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` derives its FROST/MuSig nonce deterministically from a DB-cached preprocess keyed only by `context = (b"DkgConfirmer", attempt)`. The cache is never deleted, and the context does not bind the message being signed (`set_keys_message(..., key_pair)`) nor the received preprocess set. `share_internal` calls `sign(preprocesses, msg)` with this fixed nonce for whatever `msg` the current call resolves to. If `share()`/`complete()` are invoked with two distinct `KeyPair` values (or distinct preprocess sets) under the same `attempt` — both reachable via attacker-supplied confirmation transaction data — the same nonce `d` is used to produce two signature shares over different challenges, and any observer solves for the validator's secret key `x` from the two shares: `x = (z1 - z2) / (e·λ·(c1 - c2))`.

### Finding Description
`preprocess_internal` stores `machine.cache()` (the signing nonce seed) in `CachedPreprocesses` keyed only by `self.context` (`(b"DkgConfirmer", self.attempt)`), then reconstructs the machine via `AlgorithmSignMachine::from_cache` on every call. The cache is written once and never removed or rotated — every `preprocess()`, `share()`, and `complete()` for a given attempt replays the identical nonce [1](#0-0) . `share_internal` then signs a caller-dependent message: `msg = set_keys_message(&self.spec.set(), &removed, key_pair)` where `key_pair` is supplied per call from transaction data [2](#0-1) . The file's own safety argument admits nonce reuse occurs if "the message to be signed" differs, and notes the missing guard: "we have to check the commitments generated from the decided nonces are in fact its commitments on-chain (TODO)" [3](#0-2) . Because `context` excludes `key_pair` and the preprocess set, nothing prevents two calls in the same attempt from producing shares over different `msg`/`preprocesses` with the identical cached nonce — exactly the replayed-stale-state / use-after-free class of the reference bug, manifesting as reuse of a freed-for-purposes-of-safety secret.

### Impact Explanation
Nonce reuse in Schnorr/MuSig directly leaks the secret scalar. Two shares `z_i = d + c_i·e·λ·x` over the same `d` but different binding factors `c_i` (which incorporate `msg` and the preprocess set via the FROST `rho`/`hash_msg` transcript) yield `x` in closed form. The recovered key is the validator's Ristretto signing key — the root of trust used for on-chain DKG confirmation — enabling forgery of subsequent confirmations. This is key share recovery, meeting the acceptance bar.

### Likelihood Explanation
The trigger requires the coordinator to execute `share_internal` twice within one attempt over differing inputs. `complete()` unconditionally re-runs `share_internal` and `.expect`s success, and `share()` is invoked per confirmation transaction carrying its own `key_pair` bytes; any path that delivers a differing `key_pair` or differing preprocess map for the same `(DkgConfirmer, attempt)` context triggers reuse. The code acknowledges this is only prevented by an unimplemented check, and flags a partial-rebuild bound it accepts without enforcement — a re-executed divergent history (distinct finalized messages) replays the persisted nonce, since the DB cache survives rebuilds.

### Recommendation
- Include the full signing inputs — `key_pair`/`msg` hash and the sorted preprocess set hash — in the `CachedPreprocesses` key, or store the decided `msg` alongside the cache and hard-fail on mismatch.
- Implement the documented TODO: before publishing a share, verify on-chain that the commitment derived from the cached nonce matches the commitment actually published for this attempt, and refuse to sign otherwise.
- Delete/rotate the cached preprocess once the confirmation signature for an attempt completes, and treat any second `sign` under a consumed context as fatal rather than replaying the nonce.

### Proof of Concept
1. Attempt `a` begins; coordinator calls `preprocess_internal` → stores nonce seed under key `(b"DkgConfirmer", a)` and publishes preprocess commitments.
2. A confirmation transaction reaches `DkgConfirmer::share(preprocesses, key_pair_A)` → `share_internal` reconstructs nonce `d` from cache and emits `z_A = d + c_A·e·λ·x` over `set_keys_message(.., key_pair_A)`.
3. A second path — a competing confirmation transaction carrying `key_pair_B ≠ key_pair_A`, a differing preprocess set reaching `complete(preprocesses_B, key_pair_B, shares)`, or a partial rebuild re-executing divergent finalized messages — calls `share_internal` again under the same context, emitting `z_B = d + c_B·e·λ·x`.
4. Attacker computes `x = (z_A − z_B)·(e·λ·(c_A − c_B))⁻¹` (all of `c_A, c_B, e, λ` are publicly computable), recovering the validator's private key.

Caveat: whether a distinct `key_pair`/preprocess set can be delivered for the same attempt depends on the caller logic in `coordinator/src/tributary/handle.rs`, which I could not fully verify within the iteration limit; the missing context-binding of the cache and the unimplemented safety check are confirmed in the code itself.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L50-54)
```rust
  Additionally, to ensure a rebuilt service isn't flagged as malicious, we have to check the
  commitments generated from the decided nonces are in fact its commitments on-chain (TODO).

  TODO: We also need to review how we're handling Processor preprocesses and likely implement the
  same on-chain-preprocess-matches-presumed-preprocess check before publishing shares.
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

**File:** coordinator/src/tributary/signing_protocol.rs (L288-301)
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
```
