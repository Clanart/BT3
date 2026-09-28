### Title
Cached DKG-confirmation preprocess seed keyed only by `("DkgConfirmer", attempt)` — nonce reuse across distinct tributary sets leaks the validator's private key - ([File: coordinator/src/tributary/signing_protocol.rs])

### Summary
The kernel bug caches VF `pci_dev` pointers under an index and dereferences them later without re-validating that the cached object still matches the entity being operated on. The analog in Serai is `SigningProtocol::preprocess_internal`, which caches a deterministic FROST preprocess seed in the `CachedPreprocesses` DB keyed solely by `context = (b"DkgConfirmer", attempt)`. The key does **not** include the tributary genesis, the validator set (`spec`), the MuSig participant list, or the message (`set_keys_message(set, removed, key_pair)`), even though all of those are re-derived fresh on every call. The cached seed is therefore silently reused for a *different* signing session whenever the same attempt number recurs under a different spec — which happens on every new tributary / validator-set rotation, since `removed_as_of_dkg_attempt` and the attempt counter are namespaced by `spec.genesis()` while `CachedPreprocesses` is not [1](#0-0) .

### Finding Description
`preprocess_internal` computes fresh `keys` via `musig(musig_context(self.spec.set()), self.key, participants)` on every invocation, but the nonce-generating `ChaCha20Rng` seed is loaded from `CachedPreprocesses::get(self.txn, &self.context)` where `context` is just `(b"DkgConfirmer", self.attempt)` [2](#0-1) . `AlgorithmSignMachine::from_cache` regenerates the *same* nonces `d, e` and commitments from that seed via `seeded_preprocess` (ChaCha20 seeded by the cached `[u8; 32]`) [3](#0-2) . The DB entry is never deleted after `share_internal` consumes it [4](#0-3) .

The file's own header acknowledges nonce reuse is "explicitly unsafe" and that safety relies on nonces being "context-bound" [5](#0-4)  — but the context fails to bind the spec/genesis, so two different `TributarySpec`s (e.g., a retried/rotated validator set) reusing the same `attempt` value produce identical FROST nonces `d, e` signing different `set_keys_message` messages with different binding factors `ρ` and challenges `c`.

### Impact Explanation
Each published share is `z = d + e·ρ + λ·key·c` where `ρ`, `c`, `λ` are publicly computable from the on-chain preprocesses and message. The validator's MuSig "share" here is its actual long-term private key `self.key` (the root of trust for the set). One reused seed yields one linear equation; with `k` signing sessions reusing the same seed under distinct `(ρ_i, c_i)` — accumulated passively from public tributary block data across ordinary set rotations/retries — an observer obtains `k` equations in the three unknowns `d`, `e`, `key`. At `k ≥ 3` the system uniquely determines `key`. Recovery of this key lets the attacker forge the validator's MuSig confirmations and any other signatures made with that key: full secret-key-share recovery, the highest-impact outcome.

### Likelihood Explanation
No malicious action is required to trigger reuse: it is driven by normal protocol flow. `DkgConfirmer::new` is instantiated per DKG attempt per tributary, the attempt counter is per-genesis, and `CachedPreprocesses` is a global table — so any second tributary reaching the same attempt number reuses the seed automatically [6](#0-5) . Additionally, `DkgConfirmer::share` and `DkgConfirmer::complete` each independently rebuild the machine via `share_internal`, and `complete` can be invoked with a `key_pair` differing from a prior `share` call — a second signature over a different `set_keys_message` with identical nonces [7](#0-6) .

### Recommendation
Include every input that determines the signed session in the cache key — e.g., key `CachedPreprocesses` by `(spec.genesis(), spec.set(), attempt)` or by a hash of `(genesis, participants, message)` — and/or store the derived preprocess alongside a commitment to the participant set and message, refusing to sign when they differ. Also delete the cached entry once a share has been produced (`sign` consuming it), so a stale entry can never seed a second signing.

### Proof of Concept
1. Tributary A (genesis `g_A`, validator set `V`) runs a DKG; confirmation attempt `0` begins. The coordinator calls `preprocess_internal`: no cache exists, so it generates seed `S`, stores it under key `("DkgConfirmer", 0)`, and publishes commitments `D = d·G, E = e·G`.
2. That DKG confirms `key_pair_A`; the share `z_A = d + e·ρ_A + λ_A·key·c_A` is emitted (visible in the tributary's `DkgConfirmed` transaction data). `CachedPreprocesses` retains `S`.
3. Later, tributary B (genesis `g_B`, new `set`, possibly different `removed`/`key_pair_B`) reaches DKG-confirmation attempt `0`. `preprocess_internal` finds the cache entry, decrypts `S`, and regenerates the **same** `d, e`. The published share `z_B = d + e·ρ_B + λ_B·key·c_B` signs `set_keys_message(set_B, …)` — a different message.
4. A third session at the same `(label, attempt)` key (another rotation/retry) yields `z_C`. The observer solves the 3×3 linear system over the scalar field for `(d, e, key)`, recovering the validator's private key — mirroring the CVE's stale cached object being dereferenced for an entity it no longer describes.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L25-35)
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
```

**File:** coordinator/src/tributary/signing_protocol.rs (L86-90)
```rust
create_db!(
  SigningProtocolDb {
    CachedPreprocesses: (context: &impl Encode) -> [u8; 32]
  }
);
```

**File:** coordinator/src/tributary/signing_protocol.rs (L150-181)
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
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L263-277)
```rust
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
