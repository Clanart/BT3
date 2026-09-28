### Title
Cached FROST preprocess is keyed only by (label, attempt), enabling deterministic nonce reuse across distinct signing sessions and recovery of the validator's MuSig private key - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The Hazelcast advisory's bug class — a cache that lets a later, differently-authenticated operation silently reuse state bound to an earlier identity — maps directly onto Serai's `CachedPreprocesses` DB in the coordinator's MuSig DKG-confirmation signing protocol. The cache key is `(b"DkgConfirmer", attempt)` only; it commits to neither the message being signed (`key_pair`/`removed`) nor the set of received preprocesses. Every call to `share_internal` re-derives the *same* deterministic FROST nonces from the cached seed via `AlgorithmSignMachine::from_cache` → `seeded_preprocess` → `ChaCha20Rng::from_seed(*seed.0)`. If `share`/`complete` is invoked twice under the same attempt with differing preprocesses or a differing `key_pair`, the coordinator emits two signature shares over the same nonce with different challenges — the classic FROST/Schnorr nonce-reuse failure that yields the signer's secret.

### Finding Description
`SigningProtocol::preprocess_internal` stores and retrieves the preprocess seed under `CachedPreprocesses::(context)` where `context = (b"DkgConfirmer", self.attempt)` (`coordinator/src/tributary/signing_protocol.rs:88`, `:123-145`, `:252`, `:274-277`). The seed deterministically generates all nonces (`crypto/frost/src/sign.rs:121-145`). `share_internal` then calls `preprocess_internal` again (`signing_protocol.rs:156`) — meaning each `share`/`complete` call regenerates an identical nonce set, and only the attacker-influenced inputs differ: `preprocesses` (which set the binding factors/challenge) and `msg` derived from the caller-supplied `key_pair` (`signing_protocol.rs:288-301`). The file's own header (lines 25-54) states safety depends entirely on never calling `sign` again over "distinct received messages", and flags an unimplemented `TODO` to verify regenerated commitments match what's on-chain — i.e., there is no check that the recycled nonce corresponds to the same session.

### Impact Explanation
Two shares `z1 = d + e·ρ1 + λ·s·c1` and `z2 = d + e·ρ2 + λ·s·c2` produced under the same seed share `d, e` but differ in challenge `c` whenever `msg` (different `key_pair`) or the binding factors `ρ` (different preprocess set) differ. A party observing both shares solves for the scaled secret share — here the validator's full MuSig private key (`n-of-n`, `musig()` at `signing_protocol.rs:118-121`) — and can thereafter sign as that validator. This is a full key compromise of a validator's root-of-trust key, reachable whenever the coordinator's `share` or `complete` path executes more than once under one attempt with adversary-divergent inputs (a validator can publish an alternative `key_pair` or preprocess set in its own signed transaction; the honest node re-executes `share_internal` over it).

### Likelihood Explanation
Medium-High. Exploitation requires causing a second `share`/`complete` execution for the same attempt with different transcript inputs — feasible because `share(preprocesses, key_pair)` accepts externally-supplied data and `complete` internally calls `share_internal` a second time (`signing_protocol.rs:322`). The mitigation claimed in the header (BFT finality + re-deciding nonces on rebuild) does not cover sequential invocations within one finalized attempt, and the guard the authors themselves deem necessary is an acknowledged TODO.

### Recommendation
Bind the cache context to everything the signature commits to — include the serialized `msg`/`key_pair`, the `removed` set, and a digest of the preprocess set in the `CachedPreprocesses` key (or store the produced share and return it verbatim on re-execution). Implement the noted TODO: before publishing a share, verify the regenerated preprocess commitments equal those already finalized on-chain, and abort rather than sign.

### Proof of Concept
1. Validator set runs DKG attempt `a`; honest coordinator caches seed `S` under `("DkgConfirmer", a)`.
2. Attacker's transaction supplies `key_pair_A` and preprocess set `P`; coordinator calls `DkgConfirmer::share(P, key_pair_A)` → share `z1` using nonces `N = f(S)`.
3. A second finalized transaction (or `complete` over different data) triggers `share_internal` with `key_pair_B` or `P'` → share `z2` using the same `N` but different `msg`/binding factors → different challenge.
4. `s = (z1 - z2 - e(ρ1 - ρ2)) / (λ(c1 - c2))` recovers the validator's private key. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** coordinator/src/tributary/signing_protocol.rs (L86-90)
```rust
create_db!(
  SigningProtocolDb {
    CachedPreprocesses: (context: &impl Encode) -> [u8; 32]
  }
);
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

**File:** coordinator/src/tributary/signing_protocol.rs (L150-158)
```rust
  fn share_internal(
    &mut self,
    participants: &[<Ristretto as Ciphersuite>::G],
    mut serialized_preprocesses: HashMap<Participant, Vec<u8>>,
    msg: &[u8],
  ) -> Result<(AlgorithmSignatureMachine<Ristretto, Schnorrkel>, [u8; 32]), Participant> {
    let machine = self.preprocess_internal(participants).0;

    let mut participants = serialized_preprocesses.keys().copied().collect::<Vec<_>>();
```

**File:** crypto/frost/src/sign.rs (L121-145)
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
