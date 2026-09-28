### Title
Cached FROST preprocess reused when the signing context's message/participants change, enabling nonce reuse and validator key-share recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The Astera bug is a stale-derived-state flaw: the admin changes `reserveFactor`, but the cached `currentLiquidityRate` is not recomputed, so the next accrual applies an interest split inconsistent with the new parameter.

The Serai analog is the `SigningProtocol` preprocess cache in `coordinator/src/tributary/signing_protocol.rs`. The FROST preprocess seed (from which both nonces `d` and `e` are deterministically derived via `ChaCha20Rng`) is cached keyed solely by `context = (b"DkgConfirmer", attempt)` — it is **not** bound to the participant set passed to `musig()`, the `removed` list, or the `msg` (`set_keys_message(set, removed, key_pair)`) being signed. If `share()` or `complete()` is invoked twice under the same `attempt` with different `key_pair` / participant / preprocess inputs — i.e., the "parameter" changed but the derived nonce state was not "touched" — the same `(d, e)` pair is reused to sign a different message/binding set, which leaks the validator's MuSig secret share.

### Finding Description
`preprocess_internal` stores an encrypted `CachedPreprocess` seed under `CachedPreprocesses: (context: &impl Encode) -> [u8; 32]`, where `context` is only `(b"DkgConfirmer", self.attempt)` (`signing_protocol.rs:274-277`). On every subsequent call for the same attempt, `from_cache` re-derives identical nonces from the same seed (`frost/src/sign.rs:121-144`, `seeded_preprocess` uses `ChaCha20Rng::from_seed(*seed.0)`).

`share_internal` (line 150) and `complete` (line 312) both call `preprocess_internal`/`share_internal` again, each time building `msg` from caller-supplied data: `set_keys_message(&self.spec.set(), &removed, key_pair)` (line 296-300), where `key_pair` is a parameter of the public `share`/`complete` methods driven by tributary transactions. The participant list is likewise recomputed per call from the current `removed` set (line 280, 293).

The file's own header (lines 25-48) documents that safety depends entirely on never operating on distinct messages under one context: "In order for nonce re-use to occur, the received nonce commitments (or the message to be signed) would have to be distinct and sign would have to be called again." The cache design makes exactly that catastrophic, yet nothing in the code binds the cached seed to `msg`, `removed`, or `participants` — the derived value is stale with respect to those parameters, mirroring the LendingPoolConfigurator bug where `reserveFactor` is updated while the liquidity rate stays stale.

Concretely, if a coordinator produces share `s1 = d + e·rho + c1·share_i` for `key_pair_A` and later `s2 = d + e·rho + c2·share_i` for `key_pair_B` with the same published preprocess set (same `rho`), then `share_i = (s1 - s2)/(c1 - c2)` — full recovery of the validator's MuSig signing key share.

### Impact Explanation
Recovery of a validator's MuSig secret share. Since `musig()` builds a `t = n` `ThresholdKeys` over the validator set (used to confirm DKG results on Substrate), recovering even one share contributes toward forging `set_keys` confirmations; combined with the deterministic nonces, repeated reuse across attempts with attacker-influenced `key_pair` data is equivalent to handing out equation-per-invocation on the secret. This is the "key share recovery" impact class, which alone is Critical/High. Note shares are `[u8; 32]` values published on the tributary, so both `s1` and `s2` are publicly observable.

### Likelihood Explanation
Requires two distinct `msg`/participant combinations under one `attempt` context — e.g., a `complete` transaction carrying a `key_pair` different from the one used in the earlier `share` for the same attempt, or a change to `removed` mid-attempt altering both `msg` and the MuSig mapping in `threshold_i_map_to_keys_and_musig_i_map` while the cached seed persists. The code relies on BFT message finality to prevent this rather than binding the seed to the signed inputs; whether a participant-controlled tributary transaction can actually inject a second `key_pair` within one attempt could not be fully confirmed (the transaction dispatch in `coordinator/src/tributary/handle.rs` and `transaction.rs` was not readable in this session — the grep tool returned match counts only). If the confirmation message's `key_pair` is supplied by the transaction rather than fixed by protocol state, this is directly reachable by any validator-set participant with public inputs.

### Recommendation
Bind the cached preprocess to everything that affects the signature: key `CachedPreprocesses` by `(context, msg, participants, removed)` — or hash those values into the encryption key preimage — so that any parameter change derives a fresh seed. Alternatively, derive the seed deterministically as `H(key || context || msg || participants)` and delete the cache entry after first `sign`, matching the fix pattern of "touch the dependent state when the parameter changes."

### Proof of Concept
1. Within one DKG confirmation `attempt`, the coordinator executes `DkgConfirmer::share(preprocesses, key_pair_A)`, producing and publishing share `s1`. The seed is cached under `("DkgConfirmer", attempt)`.
2. A second transaction invokes `DkgConfirmer::complete(preprocesses, key_pair_B, shares)` (or `share` with `key_pair_B`) where `key_pair_B != key_pair_A`. `preprocess_internal` loads the same seed → identical nonces `(d, e)` and identical own-commitments → identical `rho` for all parties if the preprocess set is unchanged.
3. Both shares satisfy `s_j = d + e·rho_i + c_j·λ_i·x_i` where `c_j = H(R, group_key, msg_j)`. Since `s1, s2, c1, c2` are all public/derivable, `x_i = (s1 - s2)·(c1 - c2)^{-1}·λ_i^{-1}` recovers the validator's MuSig secret share, enabling forgery of DKG-confirmation signatures. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** coordinator/src/tributary/signing_protocol.rs (L86-148)
```rust
create_db!(
  SigningProtocolDb {
    CachedPreprocesses: (context: &impl Encode) -> [u8; 32]
  }
);

struct SigningProtocol<'a, T: DbTxn, C: Encode> {
  pub(crate) key: &'a Zeroizing<<Ristretto as Ciphersuite>::F>,
  pub(crate) spec: &'a TributarySpec,
  pub(crate) txn: &'a mut T,
  pub(crate) context: C,
}

impl<T: DbTxn, C: Encode> SigningProtocol<'_, T, C> {
  fn preprocess_internal(
    &mut self,
    participants: &[<Ristretto as Ciphersuite>::G],
  ) -> (AlgorithmSignMachine<Ristretto, Schnorrkel>, [u8; 64]) {
    // Encrypt the cached preprocess as recovery of it will enable recovering the private key
    // While the DB isn't expected to be arbitrarily readable, it isn't a proper secret store and
    // shouldn't be trusted as one
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
  }
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
