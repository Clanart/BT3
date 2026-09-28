### Title
Cached FROST preprocess seed is consumed twice per signing session — nonce reuse if `share`/`complete` see divergent preprocess sets - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The external bug class is a lifetime violation: a resource (`xdna->domain`) is freed during removal yet remains reachable through a later path (`amdxdna_gem_obj_free`), producing a use-after-free. The Serai analog is a *single-use* secret resource — the cached FROST preprocess seed — whose lifetime is supposed to end after one signing session but is consumed twice: once in `DkgConfirmer::share` and again inside `DkgConfirmer::complete`. Because the machine is rebuilt deterministically from the same seed, the second `sign()` reuses identical nonces. If the preprocess set passed to `complete` differs from the one passed to `share` (the caller supplies both maps independently), the same nonce `d + e·ρ` is signed under a different binding factor/challenge, leaking the validator's private key.

### Finding Description
`SigningProtocol::preprocess_internal` loads (or creates) a `CachedPreprocess` seed keyed only by `self.context` = `(b"DkgConfirmer", attempt)` and rebuilds the sign machine via `AlgorithmSignMachine::from_cache` [1](#0-0) . `from_cache` regenerates `nonces` deterministically from `ChaCha20Rng::from_seed(*seed.0)` [2](#0-1) . The FROST spec and code explicitly state a cached preprocess "MUST only be used once. Reuse will enable third-party recovery of your private key share" [3](#0-2) .

The lifetime violation: `DkgConfirmer::share` calls `share_internal` → `preprocess_internal` → `machine.sign(preprocesses, msg)` consuming the seed [4](#0-3) . `DkgConfirmer::complete` then calls `share_internal` *again* — a second `sign()` with the same seed and same nonces [5](#0-4) . The header comment acknowledges the danger and pins safety entirely on "the nonces being context-bound under a BFT protocol," i.e., on all inputs being byte-identical between invocations [6](#0-5) .

However, the `preprocesses` `HashMap` is caller-supplied at each call site, not bound to the context. In FROST, per-participant binding factors `ρ` are computed over the signing set (`included`), so any difference in which preprocesses are present — e.g., `share` executed with exactly `t` preprocesses and `complete` executed later after additional validators' preprocesses were committed to the tributary log — changes `ρ`, the aggregate nonce commitment, and the challenge, while `d` and `e` stay identical. Two shares `(s₁ = d + e·ρ₁·λ + x·c₁)`, `(s₂ = d + e·ρ₂·λ + x·c₂)` yield a linear system recovering `x`, the private key.

### Impact Explanation
Recovery of a validator's MuSig/FROST private key (and hence their validator signing key) from two published signature shares. Shares are broadcast over authenticated channels and observed by all participants; any party holding both shares plus the public preprocesses can solve for the secret scalar. This compromises the validator's identity for all future DKG confirmations and any protocol relying on that key.

### Likelihood Explanation
Likelihood hinges on whether the tributary handler can hand `complete` a preprocess set differing from the one given to `share`. The two functions are invoked at different times against independently accumulated maps, and FROST explicitly tolerates any `t`-of-`n` subset — nothing enforces that the same subset is reused. A participant can also influence inclusion timing by when it broadcasts its preprocess, and can submit malformed preprocesses that fail `read_preprocess` in one pass but not the other (parse-failure paths return early with `Err(participant)`). I did not fully verify the `handle.rs` accumulation logic (grep confirmed `DkgConfirmer`/`share`/`complete` usage but not the exact set-construction); if the caller provably passes identical maps, the divergence requires an inconsistent local view, which narrows but does not eliminate reachability — the design deliberately delegates a cryptographic single-use invariant to an undocumented caller contract.

### Recommendation
Persist (encrypted alongside the cached seed) the exact serialized preprocess set and message used by the first `sign`, and on subsequent calls abort unless the re-supplied inputs are byte-identical — the same "on-chain-preprocess-matches-presumed-preprocess" check already flagged as a TODO in the file header. Alternatively, store the produced signature share keyed by context and re-serve it in `complete` instead of re-signing, or mark the context consumed after `share` so `complete` cannot rebuild the machine.

### Proof of Concept
Conceptual: for a fixed `(set, attempt)` context, call `DkgConfirmer::share(preprocesses_A, key_pair)` where `preprocesses_A` contains participants `{1..t}`; later call `DkgConfirmer::complete(preprocesses_B, key_pair, shares)` where `preprocesses_B` additionally includes a participant `t+1`. `share_internal` rebuilds identical nonces from `CachedPreprocesses` both times; the two emitted shares are Schnorr shares over the same `d, e` but different `ρ`/challenge. Collecting both shares plus the public preprocesses lets an observer solve `x = (s₁ - s₂ - e·λ(ρ₁ - ρ₂))/(c₁ - c₂)` — recovering the validator's private key without breaking BFT, since each individual call operated on messages that were legitimately committed.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L25-48)
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

**File:** crypto/frost/src/sign.rs (L85-87)
```rust
/// A preprocess MUST only be used once. Reuse will enable third-party recovery of your private
/// key share. Additionally, this MUST be handled with the same security as your private key share,
/// as knowledge of it also enables recovery.
```

**File:** crypto/frost/src/sign.rs (L127-141)
```rust
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
```
