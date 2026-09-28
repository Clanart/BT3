### Title
Cached FROST confirmation preprocess reused across validator sets enables MuSig nonce reuse and validator key recovery - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
CVE-2021-37957 is a use-after-free: a resource is used after its valid lifetime has ended. The Serai analog is the `CachedPreprocesses` table in the coordinator's DKG-confirmation signing protocol. A FROST preprocess has a strict single-use lifetime — reuse leaks the signer's private share — yet the cache key `context = (b"DkgConfirmer", attempt)` does not include the validator set, session, or genesis. A validator that participates in DKG confirmation for more than one validator set (e.g. set N and set N+1, both starting at `attempt = 0`) reuses the same cached 32-byte seed with the same secret key but a different message, producing a classic Schnorr/MuSig nonce reuse that allows any observer of the public tributary shares to recover the validator's private key.

### Finding Description
`SigningProtocol::preprocess_internal` stores the deterministic preprocess seed in `CachedPreprocesses`, keyed only by `self.context`, which for `DkgConfirmer` is `(&'static [u8; 12], u32)` = `(b"DkgConfirmer", attempt)` — no `spec.set()`, session, or `spec.genesis()` component [1](#0-0) . Once written, the entry is never deleted and is unconditionally reloaded and decrypted on every subsequent call [2](#0-1) . The seed deterministically regenerates identical nonces via `ChaCha20Rng::from_seed(*seed.0)` in `seeded_preprocess`, which feeds `Commitments::new` with `params.keys.original_secret_share()` [3](#0-2) . Since the MuSig keys are built directly from the long-lived validator key (`musig(musig_context(...), self.key.clone(), participants)`), the secret share input to nonce derivation is the same scalar across validator sets [4](#0-3) . The FROST spec explicitly documents that reusing a preprocess "would enable a third-party to recover your private key share" [5](#0-4) . The signed message differs per set because it is `set_keys_message(&self.spec.set(), &removed..., key_pair)` [6](#0-5) .

### Impact Explanation
Two `DkgConfirmed` shares published under the same nonce `k` but different challenges `c1`, `c2` satisfy `s1 = k + c1·x` and `s2 = k + c2·x` (modulo binding factors that are publicly computable from the on-chain preprocesses), so `x = (s1 − s2)/(c1 − c2)`. The shares are broadcast in `Transaction::DkgConfirmed` on the public tributary [7](#0-6) , so any unprivileged observer can extract the validator's MuSig secret key — the root-of-trust key used to authorize `set_keys` on Substrate. Recovery of this key lets the attacker forge DKG-confirmation signature shares and contribute to fraudulent validator-set key installations.

### Likelihood Explanation
Triggering requires only routine protocol operation: validator-set rotation causes a new DKG whose confirmation attempt numbering restarts at 0, guaranteeing a context collision for any validator persisting across sets in the same coordinator DB. No Byzantine behavior, race, or reorg is needed — the reuse happens deterministically on the second set's first confirmation. The file's own safety argument (lines 25–54) only addresses reuse *within* one BFT context and does not consider cross-set reuse, and the encryption of the cached seed is irrelevant since decryption uses the same validator key. Uncertainty: this assumes the coordinator DB and validator key persist across sets, which is the normal deployment; if keys were rotated per set the nonce would differ.

### Recommendation
Bind the cache key to the full signing context: `context = (b"DkgConfirmer", spec.genesis(), attempt)` or include `spec.set()`/session, so each validator set derives an independent preprocess. Additionally, after `share_internal` succeeds, record a `Consumed` flag (or overwrite the entry) so any second `sign` with the same seed is refused, and include the `musig_context`/participant set in the encryption-key preimage so cross-set reuse fails domain separation even if keyed identically.

### Proof of Concept
1. Validator V with coordinator key `k` participates in validator set A's DKG; `DkgConfirmer` context `("DkgConfirmer", 0)` stores seed `S` in `CachedPreprocesses`.
2. `generated_key_pair` → `share` signs `msgA = set_keys_message(setA, removedA, keyPairA)`, publishing share `sA` in `DkgConfirmed`.
3. Set rotation occurs; V (same `k`, same DB) joins set B's DKG, attempt 0. `preprocess_internal` reloads `S` (context collides) → identical nonce `k_nonce` and identical commitments.
4. `share` signs `msgB = set_keys_message(setB, removedB, keyPairB)`, publishing `sB`.
5. Observer reads both public `DkgConfirmed` shares plus the public confirmation nonces, computes each session's aggregate nonce/binding/challenge, and solves `sA − sB` for the nonce and then `k` — V's private key — exactly as documented in `spec/cryptography/FROST.md` for reused preprocesses.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L117-121)
```rust
    let algorithm = Schnorrkel::new(b"substrate");
    let keys: ThresholdKeys<Ristretto> =
      musig(musig_context(self.spec.set().into()), self.key.clone(), participants)
        .expect("signing for a set we aren't in/validator present multiple times")
        .into();
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

**File:** coordinator/src/tributary/signing_protocol.rs (L252-277)
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
```

**File:** coordinator/src/tributary/signing_protocol.rs (L288-302)
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

**File:** spec/cryptography/FROST.md (L45-62)
```markdown
# Caching

modular-frost supports caching a preprocess. This is done by having all
preprocesses use a seeded RNG. Accordingly, the entire preprocess can be derived
from the RNG seed, making the cache just the seed.

Reusing preprocesses would enable a third-party to recover your private key
share. Accordingly, you MUST not reuse preprocesses. Third-party knowledge of
your preprocess would also enable their recovery of your private key share.
Accordingly, you MUST treat cached preprocesses with the same security as your
private key share.

Since a reused seed will lead to a reused preprocess, seeded RNGs are generally
frowned upon when doing multisignature operations. This isn't an issue as each
new preprocess obtains a fresh seed from the specified RNG. Assuming the
provided RNG isn't generating the same seed multiple times, the only way for
this seeded RNG to fail is if a preprocess is loaded multiple times, which was
already a failure point.
```

**File:** coordinator/src/tributary/handle.rs (L508-546)
```rust
      Transaction::DkgConfirmed { attempt, confirmation_share, signed } => {
        let Some(removed) = removed_as_of_dkg_attempt(self.txn, genesis, attempt) else {
          self.fatal_slash(signed.signer.to_bytes(), "DkgConfirmed with an unrecognized attempt");
          return;
        };

        let data_spec =
          DataSpecification { topic: Topic::DkgConfirmation, label: Label::Share, attempt };
        match self.handle_data(&removed, &data_spec, &confirmation_share.to_vec(), &signed) {
          Accumulation::Ready(DataSet::Participating(shares)) => {
            log::info!("got all DkgConfirmed for {}", hex::encode(genesis));

            let Some(removed) = removed_as_of_dkg_attempt(self.txn, genesis, attempt) else {
              panic!(
                "DkgConfirmed for everyone yet didn't have the removed parties for this attempt",
              );
            };

            let preprocesses = ConfirmationNonces::get(self.txn, genesis, attempt).unwrap();
            // TODO: This can technically happen under very very very specific timing as the txn
            // put happens before DkgConfirmed, yet the txn commit isn't guaranteed to
            let key_pair = DkgKeyPair::get(self.txn, genesis, attempt).expect(
              "in DkgConfirmed handling, which happens after everyone \
              (including us) fires DkgConfirmed, yet no confirming key pair",
            );
            let mut confirmer = DkgConfirmer::new(self.our_key, self.spec, self.txn, attempt)
              .expect("confirming DKG for unrecognized attempt");
            let sig = match confirmer.complete(preprocesses, &key_pair, shares) {
              Ok(sig) => sig,
              Err(p) => {
                let mut tx = Transaction::RemoveParticipantDueToDkg {
                  participant: self.spec.reverse_lookup_i(&removed, p).unwrap(),
                  signed: Transaction::empty_signed(),
                };
                tx.sign(&mut OsRng, genesis, self.our_key);
                self.publish_tributary_tx.publish_tributary_tx(tx).await;
                return;
              }
            };
```
