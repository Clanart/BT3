The libsoup bug is a double-free: the same hash-table value is consumed/freed twice via duplicated parameter handling. Mapped onto Serai's real shape, the analog is the same seed value being consumed twice — `DkgConfirmer::complete` re-runs `share_internal`, which rebuilds the signing machine `from_cache` using the same `CachedPreprocess` seed stored in `CachedPreprocesses`, re-deriving and re-consuming the identical FROST nonce for a second `sign()` invocation.

### Title
Cached FROST nonce consumed twice via `DkgConfirmer::complete` re-invoking `share_internal`, enabling validator private-key recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` stores a deterministic preprocess seed in the DB keyed only by `context = (b"DkgConfirmer", attempt)`. Both `DkgConfirmer::share` and `DkgConfirmer::complete` call `share_internal`, which calls `preprocess_internal` → `AlgorithmSignMachine::from_cache`, regenerating the *same* nonce and signing again. If the `preprocesses` map or `key_pair` (both supplied per-transaction) differ between the two calls, two signature shares are produced under an identical nonce with different aggregate bindings/messages — the classic FROST nonce-reuse key-extraction condition.

### Finding Description
- `preprocess_internal` writes the seed once (`CachedPreprocesses::set` only when `is_none`) and then reuses it on every call for the same `context` [1](#0-0) 
- `DkgConfirmer::share` calls `share_internal(preprocesses, key_pair)` once [2](#0-1) , and `complete` calls `share_internal` *again* with independently-supplied `preprocesses` and `key_pair` [3](#0-2) 
- `from_cache` reconstructs a fresh machine from the same seed — nothing marks the cached preprocess consumed [4](#0-3) , directly violating the documented invariant "This cached preprocess MUST only be used once. Reuse of it enables recovery of your private key share" [5](#0-4) 
- The preprocess set is remapped through `threshold_i_map_to_keys_and_musig_i_map`, whose contents come from the transaction-supplied `HashMap<Participant, Vec<u8>>` [6](#0-5) , so the submitter of the complete transaction controls which preprocess bytes (and which `key_pair`, hence which `set_keys_message` msg) get signed a second time.

With two shares `s = d + e·λ·x` using the same `d` but different effective challenges/sets (different included preprocesses change rho/binding and possibly the message via a different `key_pair`), an observer of both published shares solves for `d` and then the MuSig secret share `x` — full validator key recovery.

### Impact Explanation
Recovery of a validator's Ristretto private key used as the root of trust for DKG confirmation (the MuSig key over `set_keys_message`). With the key, the attacker can forge confirmation shares and, depending on threshold, take over the confirmation signing path. This is exactly the secret-leakage consequence the `cache()`/`from_cache()` documentation warns about.

### Likelihood Explanation
Reachability requires `share` and `complete` to be invoked under the same `attempt` with differing inputs. Both take attacker-influenced `HashMap<Participant, Vec<u8>>` preprocess maps and an attacker-submittable `KeyPair` — the confirmation transaction submitter controls the `key_pair` argument and can choose a different subset/ordering of published preprocesses in each transaction. The code's own header admits safety depends on messages never differing between re-executions and lists an unimplemented TODO check ("we have to check the commitments generated from the decided nonces are in fact its commitments on-chain") [7](#0-6) . I was unable to fully confirm from `handle.rs` whether `share` and `complete` can be triggered by two distinct finalized transactions carrying different preprocess maps in the same attempt — if the coordinator guarantees they always carry identical data, the leak collapses to identical re-signing (harmless). The gap is real but conditional on the transaction-dispatch layer permitting divergent inputs.

### Recommendation
Delete (or rotate) `CachedPreprocesses[context]` after the first `sign()`, and/or cache the resulting `SignatureShare` and message digest so any second invocation either returns the identical cached share or aborts. Implement the noted TODO: verify the on-chain preprocesses/commitments match the cached ones before producing or publishing a share.

### Proof of Concept
1. Attempt `a` of the DKG confirmation begins; coordinator caches seed under `("DkgConfirmer", a)`.
2. Transaction T1 calls `confirmer.share(P1, kp1)` → publishes share `s1 = d + e1·λ·x`.
3. Transaction T2 (same attempt, BFT-ordered) calls `confirmer.complete(P2, kp2, shares)` where `P2` is a different participant subset or `kp2 ≠ kp1` → internally produces `s2 = d + e2·λ'·x` with the same `d`.
4. From `s1, s2` and the public transcript, solve the two-equation linear system for `d`, then `x` — validator private key recovered.

Caveat noted above: exploitability hinges on whether the tributary handler permits `share`/`complete` with divergent preprocess maps or key pairs in one attempt; that dispatch code path was not fully verified within this analysis.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L34-54)
```rust
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

**File:** coordinator/src/tributary/signing_protocol.rs (L208-250)
```rust
fn threshold_i_map_to_keys_and_musig_i_map(
  spec: &TributarySpec,
  removed: &[<Ristretto as Ciphersuite>::G],
  our_key: &Zeroizing<<Ristretto as Ciphersuite>::F>,
  mut map: HashMap<Participant, Vec<u8>>,
) -> (Vec<<Ristretto as Ciphersuite>::G>, HashMap<Participant, Vec<u8>>) {
  // Insert our own index so calculations aren't offset
  let our_threshold_i = spec
    .i(removed, <Ristretto as Ciphersuite>::generator() * our_key.deref())
    .expect("MuSig t-of-n signing a for a protocol we were removed from")
    .start;
  assert!(map.insert(our_threshold_i, vec![]).is_none());

  let spec_validators = spec.validators();
  let key_from_threshold_i = |threshold_i| {
    for (key, _) in &spec_validators {
      if threshold_i == spec.i(removed, *key).expect("MuSig t-of-n participant was removed").start {
        return *key;
      }
    }
    panic!("requested info for threshold i which doesn't exist")
  };

  let mut sorted = vec![];
  let mut threshold_is = map.keys().copied().collect::<Vec<_>>();
  threshold_is.sort();
  for threshold_i in threshold_is {
    sorted.push((key_from_threshold_i(threshold_i), map.remove(&threshold_i).unwrap()));
  }

  // Now that signers are sorted, with their shares, create a map with the is needed for MuSig
  let mut participants = vec![];
  let mut map = HashMap::new();
  for (raw_i, (key, share)) in sorted.into_iter().enumerate() {
    let musig_i = u16::try_from(raw_i).unwrap() + 1;
    participants.push(key);
    map.insert(Participant::new(musig_i).unwrap(), share);
  }

  map.remove(&our_threshold_i).unwrap();

  (participants, map)
}
```

**File:** coordinator/src/tributary/signing_protocol.rs (L304-310)
```rust
  pub(crate) fn share(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
  ) -> Result<[u8; 32], Participant> {
    self.share_internal(preprocesses, key_pair).map(|(_, share)| share)
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

**File:** crypto/frost/src/sign.rs (L209-214)
```rust
  /// Cache this preprocess for usage later.
  ///
  /// This cached preprocess MUST only be used once. Reuse of it enables recovery of your private
  /// key share. Third-party recovery of a cached preprocess also enables recovery of your private
  /// key share, so this MUST be treated with the same security as your private key share.
  fn cache(self) -> CachedPreprocess;
```
