### Title
Cached DKG confirmation nonce reused across distinct key confirmations leaks validator signing key - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The coordinator caches a FROST/MuSig preprocess seed under `("DkgConfirmer", attempt)` and never invalidates it after producing a confirmation share. Any repeated `GeneratedKeyPair` for the same `attempt` with a different `key_pair` causes the same deterministic nonces to sign a different `set_keys_message`, exposing the validator’s private signing key through nonce reuse.

### Finding Description
`SigningProtocol::preprocess_internal` derives the entire signing machine from `CachedPreprocesses` and recreates identical nonces via `AlgorithmSignMachine::from_cache` on every call. `generated_key_pair` stores the supplied `key_pair` for the attempt, loads the already-published `ConfirmationNonces`, and calls `share`, which signs `set_keys_message(..., key_pair)`. There is no check that a `key_pair` was already confirmed for that attempt, and `DkgKeyPair::set`/`KeyToDkgAttempt::set` overwrite the stored mapping before signing. A second processor `GeneratedKeyPair` for the same `attempt` therefore reuses the same cached nonce under a new challenge/message. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

### Impact Explanation
For Schnorr-style shares, two signatures using the same nonce `r` over different challenges reveal the secret: `s1 = r + c1*x` and `s2 = r + c2*x` imply `x = (s1 - s2) / (c1 - c2)`. Here the secret is the validator’s Ristretto signing key used as the coordinator’s root-of-trust MuSig input, so an attacker who triggers two distinct confirmed key pairs for one DKG attempt can recover that validator key and forge validator signatures. This is key-share/key recovery reachable from attacker-influenced public protocol messages. [6](#0-5) [7](#0-6) 

### Likelihood Explanation
The path only requires that a processor emit more than one `GeneratedKeyPair` for the same `KeyGenId.attempt` with different `substrate_key`/`network_key` values; the coordinator handler explicitly lacks validation of the `KeyGenId` fields and directly signs each supplied pair. BFT finality does not prevent this because the differing messages are separate processor-to-coordinator events handled before a single confirmation key is locked in. The code documents that reusing this cached preprocess leaks the private key share. [8](#0-7) [9](#0-8) [10](#0-9) 

### Recommendation
Bind the cached preprocess lifecycle to exactly one confirmation message: after `DkgConfirmer::share` succeeds, delete or mark `CachedPreprocesses` and `ConfirmationNonces` as consumed for that attempt, and make `generated_key_pair` reject any second `key_pair` for the same `attempt` instead of overwriting `DkgKeyPair`. Additionally validate `KeyGenId` fields before signing and persist a monotonic “confirmation share emitted” flag so replays/reorgs cannot rebuild the same machine for a different `set_keys_message`. [3](#0-2) [4](#0-3) 

### Proof of Concept
1. During DKG attempt `a`, validators publish confirmation preprocesses; `dkg_confirmation_nonces` stores/returns the coordinator’s deterministic preprocess from `CachedPreprocesses[("DkgConfirmer", a)]`.
2. A processor sends `GeneratedKeyPair { id.attempt: a, substrate_key: K1, network_key: N1 }`; `generated_key_pair` stores `(K1, N1)` and emits `share1` for `set_keys_message(..., K1/N1)` using nonce `r`.
3. The same processor sends `GeneratedKeyPair { id.attempt: a, substrate_key: K2, network_key: N2 }`; the handler again stores the new pair and calls `share`, rebuilding the same cached seed and reusing `r` for a different challenge.
4. With public `share1`, `share2`, `K1/N1`, `K2/N2`, and the fixed preprocesses, solve `x = (share1 - share2) * inverse(challenge1 - challenge2)`, recovering the coordinator validator’s private signing key. [11](#0-10) [12](#0-11) [13](#0-12)

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L16-35)
```rust
  Instead of maintaining state in memory, a combination of the DB and re-execution are used. This
  is deemed acceptable re: performance as:

  1) This is only done prior to a DKG being confirmed on Substrate and is assumed infrequent.
  2) This is an O(n) algorithm.
  3) The size of the validator set is bounded by MAX_KEY_SHARES_PER_SET.

  Accordingly, this should be tolerable.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L288-309)
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
  // Get the share for this confirmation, if the preprocesses are valid.
  pub(crate) fn share(
    &mut self,
    preprocesses: HashMap<Participant, Vec<u8>>,
    key_pair: &KeyPair,
  ) -> Result<[u8; 32], Participant> {
    self.share_internal(preprocesses, key_pair).map(|(_, share)| share)
```

**File:** coordinator/src/tributary/handle.rs (L36-59)
```rust
pub fn dkg_confirmation_nonces(
  key: &Zeroizing<<Ristretto as Ciphersuite>::F>,
  spec: &TributarySpec,
  txn: &mut impl DbTxn,
  attempt: u32,
) -> [u8; 64] {
  DkgConfirmer::new(key, spec, txn, attempt)
    .expect("getting DKG confirmation nonces for unknown attempt")
    .preprocess()
}

pub fn generated_key_pair<D: Db>(
  txn: &mut D::Transaction<'_>,
  key: &Zeroizing<<Ristretto as Ciphersuite>::F>,
  spec: &TributarySpec,
  key_pair: &KeyPair,
  attempt: u32,
) -> Result<[u8; 32], Participant> {
  DkgKeyPair::set(txn, spec.genesis(), attempt, key_pair);
  KeyToDkgAttempt::set(txn, key_pair.0 .0, &attempt);
  let preprocesses = ConfirmationNonces::get(txn, spec.genesis(), attempt).unwrap();
  DkgConfirmer::new(key, spec, txn, attempt)
    .expect("claiming to have generated a key pair for an unrecognized attempt")
    .share(preprocesses, key_pair)
```

**File:** coordinator/src/main.rs (L501-535)
```rust
        key_gen::ProcessorMessage::GeneratedKeyPair { id, substrate_key, network_key } => {
          // TODO2: Check the KeyGenId fields

          // Tell the Tributary the key pair, get back the share for the MuSig signature
          let share = crate::tributary::generated_key_pair::<D>(
            &mut txn,
            key,
            spec,
            &KeyPair(Public::from(substrate_key), network_key.try_into().unwrap()),
            id.attempt,
          );

          // TODO: Move this into generated_key_pair?
          match share {
            Ok(share) => {
              vec![Transaction::DkgConfirmed {
                attempt: id.attempt,
                confirmation_share: share,
                signed: Transaction::empty_signed(),
              }]
            }
            Err(p) => {
              let participant = spec
                .reverse_lookup_i(
                  &crate::tributary::removed_as_of_dkg_attempt(&txn, spec.genesis(), id.attempt)
                    .expect("participating in DKG attempt yet we didn't save who was removed"),
                  p,
                )
                .unwrap();
              vec![Transaction::RemoveParticipantDueToDkg {
                participant,
                signed: Transaction::empty_signed(),
              }]
            }
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

**File:** crypto/frost/src/sign.rs (L320-409)
```rust
    let nonces = self.params.algorithm.nonces();
    #[allow(non_snake_case)]
    let mut B = BindingFactor(HashMap::<Participant, _>::with_capacity(included.len()));
    {
      // Parse the preprocesses
      for l in &included {
        {
          self
            .params
            .algorithm
            .transcript()
            .append_message(b"participant", C::F::from(u64::from(u16::from(*l))).to_repr());
        }

        if *l == self.params.keys.params().i() {
          let commitments = self.preprocess.commitments.clone();
          commitments.transcript(self.params.algorithm.transcript());

          let addendum = self.preprocess.addendum.clone();
          {
            let mut buf = vec![];
            addendum.write(&mut buf).unwrap();
            self.params.algorithm.transcript().append_message(b"addendum", buf);
          }

          B.insert(*l, commitments);
          self.params.algorithm.process_addendum(&view, *l, addendum)?;
        } else {
          let preprocess = preprocesses.remove(l).unwrap();
          preprocess.commitments.transcript(self.params.algorithm.transcript());
          {
            let mut buf = vec![];
            preprocess.addendum.write(&mut buf).unwrap();
            self.params.algorithm.transcript().append_message(b"addendum", buf);
          }

          B.insert(*l, preprocess.commitments);
          self.params.algorithm.process_addendum(&view, *l, preprocess.addendum)?;
        }
      }

      // Re-format into the FROST-expected rho transcript
      let mut rho_transcript = A::Transcript::new(b"FROST_rho");
      rho_transcript.append_message(b"group_key", self.params.keys.group_key().to_bytes());
      rho_transcript.append_message(b"message", C::hash_msg(msg));
      rho_transcript.append_message(
        b"preprocesses",
        C::hash_commitments(self.params.algorithm.transcript().challenge(b"preprocesses").as_ref()),
      );

      // Generate the per-signer binding factors
      B.calculate_binding_factors(&rho_transcript);

      // Merge the rho transcript back into the global one to ensure its advanced, while
      // simultaneously committing to everything
      self
        .params
        .algorithm
        .transcript()
        .append_message(b"rho_transcript", rho_transcript.challenge(b"merge"));
    }

    #[allow(non_snake_case)]
    let Rs = B.nonces(&nonces);

    let our_binding_factors = B.binding_factors(multisig_params.i());
    let nonces = self
      .nonces
      .drain(..)
      .enumerate()
      .map(|(n, nonces)| {
        let [base, mut actual] = nonces.0;
        *actual *= our_binding_factors[n];
        *actual += base.deref();
        actual
      })
      .collect::<Vec<_>>();

    let share = self.params.algorithm.sign_share(&view, &Rs, nonces, msg);

    Ok((
      AlgorithmSignatureMachine {
        params: self.params,
        view,
        B,
        Rs,
        share,
        blame_entropy: self.blame_entropy,
      },
      SignatureShare(share),
```
