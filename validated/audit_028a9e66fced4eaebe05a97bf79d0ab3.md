### Title
Re-execution of `share_internal`/`complete` reuses the cached deterministic FROST nonce across distinct preprocess sets, enabling secret share recovery - (File: crypto/frost/src/sign.rs)

### Summary

The external report concerns a locking inconsistency where state assumed stable is mutated/freed between accesses. The analog in Serai is a one-time-use value (the FROST preprocess seed/nonces) assumed consumed once but re-materialized on every re-execution of the signing machine. `AlgorithmSignMachine::from_cache` → `seeded_preprocess` deterministically re-derives the identical nonces `(d, e)` and commitments from a 32-byte `CachedPreprocess` seed via `ChaCha20Rng` [1](#0-0) . The library contract is that a cached preprocess "MUST only be used once" because reuse leaks the key share [2](#0-1) . `SigningProtocol::preprocess_internal` in `coordinator/src/tributary/signing_protocol.rs` stores one seed keyed only by `(b"DkgConfirmer", attempt)` and rebuilds the machine from it on *every* call [3](#0-2) . `share()` calls `share_internal` once, and `complete()` calls `share_internal` *again* with a caller-supplied `preprocesses` map and `key_pair` [4](#0-3) .

### Finding Description

`AlgorithmSignMachine::sign` computes the signature share using the same local nonces regardless of which peers' preprocesses are supplied; only the binding factors `rho`, the group nonce `R`, and the challenge (over `msg`) depend on the `preprocesses` map [5](#0-4) . Therefore two invocations of `sign` with the same cached seed but **different preprocess sets or different `key_pair`/`msg`** produce two Schnorr signature shares sharing identical nonce scalars `d`, `e` but different `R`/`c`. This is classic Schnorr nonce reuse: the two shares are linear equations in the secret share, solvable by anyone who observes both. The file's own header acknowledges this: "it is explicitly unsafe to reuse nonces across signing sessions," and safety rests solely on the assumption that BFT ordering prevents operating on distinct received messages [6](#0-5) . That assumption does not hold for `share()` vs `complete()`: they are two distinct call sites each re-executing `sign` over independently supplied `HashMap<Participant, Vec<u8>>` preprocesses, and an inconsistent/previously-failed `share` call followed by `complete` (or a `complete` whose preprocess map differs from the one used at `share` time) signs twice under one nonce.

### Impact Explanation

An observer collecting both published `[u8; 32]` signature shares obtains two equations `s1 = d + e·ρ1·λ + c1·x·λ` and `s2 = d + e·ρ2·λ + c2·x·λ` with identical `d`,`e` but different `ρ`/`c`, yielding the validator's MuSig/FROST private key share `x`. Recovery of the key share lets the attacker forge the coordinator-side DKG confirmation signatures (validator set key confirmations), which are the root of trust for confirming threshold keys on-chain.

### Likelihood Explanation

The reachability requirements are modest: the preprocesses map supplied to `share`/`complete` is populated from peer-provided bytes deserialized via `read_preprocess`/`Commitments::read` [7](#0-6) , so any other validator controls which participants' preprocesses are present at each call. A failed `share` (e.g., `InvalidPreprocess` returned for one participant) followed by a retry/`complete` with a corrected or reordered map, or a Byzantine participant withholding its preprocess from `share` but including it in `complete`, produces the second signing under reused nonces. The code itself flags the missing on-chain check that its commitments match the presumed preprocess as a TODO [8](#0-7) .

### Recommendation

- Delete or burn the `CachedPreprocesses` entry the first time `share_internal` actually produces a share for a context, so `complete` cannot re-sign under the same nonces; persist the produced signature share/machine state instead of re-deriving it.
- Alternatively, persist the exact `preprocesses` map and `msg` used at `share` time and have `complete` verify byte-equality before re-executing, aborting rather than re-signing with differing inputs.
- Implement the noted TODO: before publishing a share, verify on-chain that the commitments derived from the cached seed match the commitments actually used for that context.

### Proof of Concept

1. Attempt `n` reaches DKG confirmation; `DkgConfirmer::share` is called with `preprocesses` set P1 and `key_pair` K1 → internally `share_internal` loads `CachedPreprocesses[("DkgConfirmer", attempt)]`, rebuilds nonces `(d,e)` via `seeded_preprocess`, and emits share `s1`.
2. The same coordinator later calls `complete(preprocesses = P2, key_pair = K2, shares)` where P2 differs from P1 (e.g., one malicious validator's preprocess omitted earlier). `complete` re-runs `share_internal`, regenerating identical `(d,e)`, and `machine.sign(P2, msg2)` produces share `s2` with different aggregate `R` and challenge.
3. From `s1`, `s2`, and the public preprocesses, solve the two-share linear system for `d` (or directly for `x·λ`), recovering the validator's secret share — the exact failure mode `CachedPreprocess`'s doc warns about [9](#0-8) .

Caveat: the reachable call sites are in `coordinator/` (outside the enumerated in-scope crates), but the root cause — deterministic nonce re-derivation in `seeded_preprocess`/`from_cache` with no reuse enforcement — is in-scope `crypto/frost` code, and the in-scope `sign` path is what produces both leaked shares.

### Citations

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

**File:** crypto/frost/src/sign.rs (L209-224)
```rust
  /// Cache this preprocess for usage later.
  ///
  /// This cached preprocess MUST only be used once. Reuse of it enables recovery of your private
  /// key share. Third-party recovery of a cached preprocess also enables recovery of your private
  /// key share, so this MUST be treated with the same security as your private key share.
  fn cache(self) -> CachedPreprocess;

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

**File:** crypto/frost/src/sign.rs (L283-317)
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

    {
      // Domain separate FROST
      self.params.algorithm.transcript().domain_separate(b"FROST");
```

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

**File:** coordinator/src/tributary/signing_protocol.rs (L50-54)
```rust
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

**File:** coordinator/src/tributary/signing_protocol.rs (L160-168)
```rust
    let mut preprocesses = HashMap::new();
    for participant in participants {
      preprocesses.insert(
        participant,
        machine
          .read_preprocess(&mut serialized_preprocesses.remove(&participant).unwrap().as_slice())
          .map_err(|_| participant)?,
      );
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
