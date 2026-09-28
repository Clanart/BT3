### Title
FROST nonce reuse via persistent CachedPreprocesses — share() and complete() each re-sign with the same cached nonce (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` stores the FROST preprocess (nonce seed) in `CachedPreprocesses` keyed only by `context`, and never deletes it after use — the exact "state left in place when it should have been removed" error-handling flaw from ALPINE-CVE-2024-31145. `DkgConfirmer::share` and `DkgConfirmer::complete` each invoke `share_internal`, which reloads the identical cached nonce and calls `machine.sign(...)` again. The FROST API contract requires the cached preprocess to be deleted after a single use ("Any reuse will presumably cause the signer to leak their secret share"). [1](#0-0) [2](#0-1) 

### Finding Description
The cache key is `context = (b"DkgConfirmer", self.attempt)` [3](#0-2) . Within one attempt there is no code path removing the DB entry: `share()` calls `share_internal` → `sign(preprocesses, msg)`, and `complete()` calls `share_internal` a second time, re-signing with the same recovered nonce [4](#0-3) . Safety therefore rests entirely on both calls receiving byte-identical `preprocesses` and `key_pair`. But `complete` takes its own `preprocesses` map and `key_pair` arguments; any divergence — different participant set or different `KeyPair` (the message `set_keys_message` binds the key pair) — produces a second Schnorr share `s2 = k + c2·x` alongside the already-broadcast `s1 = k + c1·x`, with `c1 ≠ c2` since the FROST binding challenge covers the preprocess set and message. Solving the linear pair recovers the validator's MuSig secret share `x = (s1−s2)/(c1−c2)`. The file itself documents that fixed-nonce safety depends on messages never diverging, and lists the needed consistency check as an unimplemented TODO [5](#0-4) .

### Impact Explanation
Reuse of a FROST nonce across two differing challenges yields direct algebraic recovery of the signer's secret key share. Here the victim key is a validator's MuSig key — the root-of-trust key used for on-chain DKG confirmation — so recovery compromises validator-set key confirmation security.

### Likelihood Explanation
No deletion of `CachedPreprocesses` exists anywhere in the codebase; the only guard is the assumption that re-execution inputs are identical. `share` and `complete` are separate entry points taking attacker-influenced inputs (`preprocesses` maps, `key_pair`), so any path where the two calls see distinct preprocess sets or key pairs — e.g., a retry after partial failure, or `complete` invoked with a different `KeyPair` — triggers reuse. This is a conditional but real key-recovery path.

### Recommendation
Delete the `CachedPreprocesses` entry (e.g., `CachedPreprocesses::del`) when `share_internal` succeeds, so a second `sign` cannot reload the same nonce. Alternatively, make `complete` reuse the `SignatureMachine`/share produced by `share` rather than re-signing, and add the noted TODO check that on-chain preprocess commitments match the presumed preprocess before publishing a share.

### Proof of Concept
1. `DkgConfirmer::share(preprocesses_A, key_pair_1)` emits share `s1` computed with cached nonce `k` (stored under context `(b"DkgConfirmer", attempt)`).
2. `DkgConfirmer::complete(preprocesses_B, key_pair_1, shares)` (or the same with `key_pair_2`) calls `share_internal` again; `preprocess_internal` reloads the same undeleted cache entry, so `sign` runs with the same `k` but a different binding challenge `c2`.
3. From the two public shares, compute `x = (s1 − s2) / (c1 − c2)` — the validator's secret share.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L25-54)
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

**File:** coordinator/src/tributary/signing_protocol.rs (L274-277)
```rust
  fn signing_protocol(&mut self) -> DkgConfirmerSigningProtocol<'_, T> {
    let context = (b"DkgConfirmer", self.attempt);
    SigningProtocol { key: self.key, spec: self.spec, txn: self.txn, context }
  }
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
