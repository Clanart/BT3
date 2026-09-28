### Title
Cached FROST preprocess is reused on every `share_internal` call for the same context, enabling nonce reuse and key share recovery — (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` persists a single `CachedPreprocess` per `context` (e.g., `(b"DkgConfirmer", attempt)`) and re-derives the *same* deterministic FROST nonce every time it runs for that context (`coordinator/src/tributary/signing_protocol.rs:123-145`). `share_internal` calls `preprocess_internal` each time it is invoked (`signing_protocol.rs:156`), signs, and returns `Err(participant)` on `InvalidPreprocess`/`InvalidShare` (`signing_protocol.rs:177`). Nothing records that a signature share was already produced for a context, so a second call under the same attempt with a different preprocess set (e.g., after dropping a faulty participant's preprocess) signs with the identical nonce `d` but a different binding challenge `e`. The two public signature shares then allow recovery of the validator's private MuSig/FROST key share — an unauthorized disclosure of secret data, matching the confidentiality-impact class of CVE-2022-39402.

### Finding Description
- `CachedPreprocesses::set` is only written when no cache exists for the context, so the cached nonce is reused for the lifetime of the context [1](#0-0) .
- `share_internal` re-invokes `preprocess_internal` and then `machine.sign(preprocesses, msg)`, returning `Err(Participant)` on failure — leaving the door open for the caller to retry the same attempt with a modified preprocess map [2](#0-1) .
- `DkgConfirmer::share` / `share_internal` are driven by externally supplied `preprocesses` maps and `key_pair` inputs; the message (`set_keys_message`) and the signer set both feed the FROST binding factor, so a differing input under the same `attempt` produces a distinct challenge over the same nonce [3](#0-2) .
- The file's own header acknowledges nonce reuse is "explicitly unsafe" and relies solely on BFT single-finality of inputs; it does not prevent two `share` executions with different inputs in the same attempt (e.g., first attempt errors on `InvalidParticipant`, then retry) [4](#0-3) .

### Impact Explanation
With two published shares `s1 = d + λ·x·e1`, `s2 = d + λ·x·e2` over the same nonce, `x = (s1 − s2)/(λ(e1 − e2))` recovers the validator's threshold key share. Shares and the resulting MuSig signature are public (on-chain / broadcast), so any unprivileged observer can perform the recovery — unauthorized read access to secret key material, plus the ability to forge future contributions for that share.

### Likelihood Explanation
Requires `share`/`share_internal` to be invoked more than once for the same context with differing inputs — plausible via the `Err(Participant)` retry path or an operator re-executing after failure — but not proven in the reviewed code that the caller actually retries; the design intends BFT finality to make inputs unique. Medium likelihood; exploitability hinges on coordinator retry behavior I could not fully trace.

### Recommendation
Record a "share produced" flag per context in the DB (alongside `CachedPreprocesses`) and refuse to sign a second time for the same context, or bind the cached preprocess to a hash of the exact `preprocesses`/`msg` signed and panic/abort on mismatch. Also implement the noted TODO of verifying the published preprocess matches the presumed one before publishing shares [5](#0-4) .

### Proof of Concept
1. Validator participates in a DKG confirmation for `attempt = N`; `preprocess()` stores `CachedPreprocess` under context `(b"DkgConfirmer", N)`.
2. `share(preprocesses_A, key_pair)` is called; one entry is an invalid preprocess → returns `Err(p)` after `preprocess_internal` already regenerated the same nonce machine.
3. `share(preprocesses_B, key_pair)` (faulty participant removed, or a differing `key_pair`/message) is called for the same `attempt`; the same nonce `d` signs a different binding value `e2`, and the share is published.
4. Observer collects the two shares and the two binding challenges and solves `x = (s1 − s2)/(λ(e1 − e2))`, recovering the validator's Ristretto key share.

Caveat: step 2–3 requires the coordinator/tributary caller to retry `share` within one attempt, which I could not confirm from the reviewed call sites; if callers abort the attempt on `Err`, the exposure is latent rather than reachable.

### Citations

**File:** coordinator/src/tributary/signing_protocol.rs (L25-55)
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
*/
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

**File:** coordinator/src/tributary/signing_protocol.rs (L155-181)
```rust
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

**File:** coordinator/src/tributary/signing_protocol.rs (L288-310)
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
  }
```
