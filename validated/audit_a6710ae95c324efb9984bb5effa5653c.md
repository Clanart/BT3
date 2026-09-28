### Title
Cached FROST preprocess seed persists in the DB across signing contexts, enabling nonce reuse and validator key recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` stores a `CachedPreprocess` (a 32-byte ChaCha20 seed that deterministically derives all FROST nonces) in the database keyed only by `context` — for the DKG confirmer, `("DkgConfirmer", attempt)` — and never deletes or rotates it. This is the direct analog of the KVM bug: the resource (nonce seed) remains "attached" (the DB entry keeps pointing to it) after the signing operation that consumed it completes, so a later execution path silently reuses an invalidated nonce. `crypto/frost/src/sign.rs` documents that `from_cache` requires the preprocess "must be deleted so it's never reused. Any reuse will presumably cause the signer to leak their secret share" — the coordinator never deletes it. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`preprocess_internal` checks `CachedPreprocesses::get(self.txn, &self.context)`; if absent it generates a fresh machine, XOR-encrypts `machine.cache()` under a key derived from `"Cached Preprocess Encryption Key" || context || validator_secret`, and stores it. On every subsequent call it loads, decrypts, and calls `AlgorithmSignMachine::from_cache(algorithm, keys, CachedPreprocess(cached))`, which feeds the seed into `ChaCha20Rng::from_seed` and regenerates the identical `(d, e)` nonce pair and identical commitments via `seeded_preprocess`. [4](#0-3) [5](#0-4) 

Two properties make the stale entry dangerous:

1. **The context is not scoped to the validator set or tributary instance.** `DkgConfirmer::signing_protocol` sets `context = (b"DkgConfirmer", self.attempt)`, where `attempt` is a small counter that resets every DKG session. Neither the session, the genesis, nor the participant set is in the DB key or in the encryption-key preimage beyond `self.key` (the coordinator's persistent validator key). When the same validator participates in a later DKG confirmation at the same `attempt` number — normal operation across sessions — the same context decrypts to the same seed, producing identical nonces for a *different* `set_keys_message` (new key pair, new removed list). [6](#0-5) 

2. **`share` and `complete` both re-run `sign` on the same nonce.** `DkgConfirmer::complete` calls `share_internal` again to rebuild the signature machine. If the `key_pair` or preprocess set presented to `complete` differs from what was used for `share` (they are supplied per-call from on-chain data), the same nonce signs a different message/binding-factor set. [7](#0-6) 

The file's own header acknowledges "it is explicitly unsafe to reuse nonces across signing sessions" and derives safety solely from BFT message ordering and the "no partial rebuild" bound — i.e., the code intentionally keeps the seed alive for re-execution, but nothing invalidates it once the confirmation completes, exactly like `vmcs01` retaining a pointer to the freed VMCS. [8](#0-7) 

### Impact Explanation
FROST signature share `s_i = d + b·e + λ_i·c·x_i`. Two shares produced with the same `(d, e)` but different binding factors `b`, challenges `c`, or messages allow solving for the MuSig secret share `x_i` — here the validator's personal Ristretto signing key, the root-of-trust key used for all MuSig-confirmed operations. Two completed `set_keys_message` signatures under a reused nonce across two DKG sessions are publicly visible on Substrate, so any observer recovers the validator's private key. This satisfies the "key share recovery" impact bar; severity High. [9](#0-8) 

### Likelihood Explanation
No adversarial input is strictly required: the reuse triggers whenever the same coordinator key reaches a second DKG confirmation at a repeated `attempt` index, which occurs in the normal course of validator-set rotations. An unprivileged observer only needs the two resulting signatures. Exploitability within a *single* session additionally requires `share` and `complete` to see divergent inputs; the code relies on BFT determinism for that, so the cross-session path is the reliable trigger. Likelihood Medium-High given key reuse is the designed behavior.

### Recommendation
Bind the cached seed to the full signing context (genesis + session + attempt + participant set), and delete or tombstone `CachedPreprocesses` once `complete` succeeds so the entry can never service a new session — mirroring the fix of hiding/invalidating the VMCS at its explicit VMCLEAR rather than leaving a live pointer. Alternatively, mix a monotonic per-session value into both the DB key and the encryption-key preimage so a stale entry cannot decrypt to a valid seed.

### Proof of Concept
1. Validator V with persistent key `k` participates in DKG confirmation at `attempt = 0` for session S1. `preprocess_internal` stores seed `σ` under `("DkgConfirmer", 0)` and emits share/signature for `set_keys_message(set1, [], kp1)`.
2. Later, V participates in a new DKG at `attempt = 0` for session S2. `CachedPreprocesses::get` hits, `from_cache` regenerates identical `(d, e)` and identical commitments.
3. V signs `set_keys_message(set2, removed2, kp2)` — different message, same nonce.
4. Given the two shares `s1 = d + b1·e + λ·c1·k` and `s2 = d + b2·e + λ·c2·k` (with `b`, `c`, `λ` all computable from public transcript inputs), solve the 2×2 linear system for `k`.

Caveat I could not fully verify within this pass: whether coordinator keys or the DB are wiped between sessions, and whether `attempt` can collide across sessions in practice depends on `removed_as_of_dkg_attempt` lifecycle in `coordinator/src/tributary/`. The within-session variant (divergent inputs to `share` vs `complete`) is mitigated only by the documented BFT-finality assumption, not by any deletion of the consumed seed.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L1-14)
```rust
/*
  A MuSig-based signing protocol executed with the validators' keys.

  This is used for confirming the results of a DKG on-chain, an operation requiring all validators
  which aren't specified as removed while still satisfying a supermajority.

  Since we're using the validator's keys, as needed for their being the root of trust, the
  coordinator must perform the signing. This is distinct from all other group-signing operations,
  as they're all done by the processor.

  The MuSig-aggregation achieves on-chain efficiency and enables a more secure design pattern.
  While we could individually tack votes, that'd require logic to prevent voting multiple times and
  tracking the accumulated votes. MuSig-aggregation simply requires checking the list is sorted and
  the list's weight exceeds the threshold.
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

**File:** coordinator/src/tributary/signing_protocol.rs (L86-90)
```rust
create_db!(
  SigningProtocolDb {
    CachedPreprocesses: (context: &impl Encode) -> [u8; 32]
  }
);
```

**File:** coordinator/src/tributary/signing_protocol.rs (L107-147)
```rust
    let mut encryption_key = {
      let mut encryption_key_preimage =
        Zeroizing::new(b"Cached Preprocess Encryption Key".to_vec());
      encryption_key_preimage.extend(self.context.encode());
      let repr = Zeroizing::new(self.key.to_repr());
      encryption_key_preimage.extend(repr.deref());
      Blake2s256::digest(&encryption_key_preimage)
    };
    let encryption_key_slice: &mut [u8] = encryption_key.as_mut();

    let algorithm = Schnorrkel::new(b"substrate");
    let keys: ThresholdKeys<Ristretto> =
      musig(musig_context(self.spec.set().into()), self.key.clone(), participants)
        .expect("signing for a set we aren't in/validator present multiple times")
        .into();

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

**File:** coordinator/src/tributary/signing_protocol.rs (L252-327)
```rust
type DkgConfirmerSigningProtocol<'a, T> = SigningProtocol<'a, T, (&'static [u8; 12], u32)>;

pub(crate) struct DkgConfirmer<'a, T: DbTxn> {
  key: &'a Zeroizing<<Ristretto as Ciphersuite>::F>,
  spec: &'a TributarySpec,
  removed: Vec<<Ristretto as Ciphersuite>::G>,
  txn: &'a mut T,
  attempt: u32,
}

impl<T: DbTxn> DkgConfirmer<'_, T> {
  pub(crate) fn new<'a>(
    key: &'a Zeroizing<<Ristretto as Ciphersuite>::F>,
    spec: &'a TributarySpec,
    txn: &'a mut T,
    attempt: u32,
  ) -> Option<DkgConfirmer<'a, T>> {
    // This relies on how confirmations are inlined into the DKG protocol and they accordingly
    // share attempts
    let removed = crate::tributary::removed_as_of_dkg_attempt(txn, spec.genesis(), attempt)?;
    Some(DkgConfirmer { key, spec, removed, txn, attempt })
  }
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

**File:** crypto/frost/src/sign.rs (L121-143)
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
