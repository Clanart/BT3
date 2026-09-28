### Title
Cached FROST preprocess is not bound to the participant set or message, enabling nonce reuse and validator key recovery on re-executed signing rounds - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
The OAuth downgrade bug class is: a caller requests a strong binding (1.0a + `oauth_verifier`), and a later step silently drops that binding, downgrading the security of the exchange without error. In Serai's coordinator MuSig/FROST signing protocol, the analog is the deterministic cached preprocess in `SigningProtocol::preprocess_internal`: the nonce seed is cached keyed only by `context`, and `share_internal`/`complete` re-execute `machine.sign` each call. Nothing binds the cached nonces to the participant set or the message actually signed, so any re-execution of `share_internal` for the same context over different preprocesses/messages reuses the same nonces.

### Finding Description
`preprocess_internal` derives the FROST preprocess deterministically from `CachedPreprocesses::get(self.txn, &self.context)`, where context is e.g. `(b"DkgConfirmer", self.attempt)` [1](#0-0) . `share_internal` rebuilds the machine from that seed and calls `machine.sign(preprocesses, msg)` [2](#0-1) . The MuSig `keys` are rebuilt per call from the `participants` argument, which is derived from the submitted preprocess map (`threshold_i_map_to_keys_and_musig_i_map` [3](#0-2) ), so the binding factors `rho` and the challenge vary with the signer set and `msg` [4](#0-3) .

Because the nonce `(d, e)` comes from the cached seed, two `share_internal` executions under the same context with different inputs produce shares `s1 = d + rho1·e + c1·x` and `s2 = d + rho2·e + c2·x`. The file's own header acknowledges: "it is explicitly unsafe to reuse nonces across signing sessions" and safety relies on received messages being BFT-finalized and never re-signed differently [5](#0-4) . The documented "TODO: verify the on-chain preprocess matches the presumed preprocess" check is not implemented — `share`/`complete` never verify that the preprocess being signed matches the commitment published on the Tributary, mirroring the OAuth case where the verifier parameter is silently not enforced.

### Impact Explanation
If `share`/`complete` is invoked twice for one context with different preprocess sets or a different `key_pair`/message (distinct `key_pair` values via `DkgKeyPair::set` overwrite paths [6](#0-5) , or a retried round with a different signer subset), the two broadcast shares leak `e` and, when `c1 != c2`, the validator's MuSig secret key `x` (solving the two-equation linear system). Compromise of a validator key enables forging `set_keys`/signing shares for that validator — key share recovery, the in-scope impact class.

### Likelihood Explanation
Safety depends entirely on the invariant that `share_internal` only ever runs once per context on BFT-agreed inputs. Reboots, re-delivery of `DkgConfirmed`/`GenerateKeyPair` flows, or differing preprocess maps (e.g., a first attempt aborting on `InvalidShare` then retrying with a different subset under the same attempt context) violate it without any error being raised — the same silent-downgrade shape as the report. It does not require a malicious validator or broken BFT as an assumption; it requires a logical path to re-signing, which the code itself lists as the failure mode. I could not fully confirm a concrete external path that forces a *second* `share` call with different inputs in normal operation, so likelihood is moderate.

### Recommendation
Bind the cache to the effective inputs: include a hash of `participants` (and the message/commitment set) in the cached-preprocess context or in the machine parameters checked before `sign`; and implement the documented check that the locally regenerated preprocess equals the commitment actually published/used on-chain before emitting a share. Alternatively, refuse to sign if the reconstructed commitments differ from the ones whose preprocess was broadcast for this context.

### Proof of Concept
1. Context `(b"DkgConfirmer", attempt)` creates a cached seed; validator publishes its preprocess.
2. Path A: `share_internal(preprocesses_A, msg_A)` → broadcast share `s1 = d + rho_A·e + c_A·x`.
3. Path B (re-execution with distinct inputs — different signer subset or different `key_pair`): `share_internal(preprocesses_B, msg_B)` reuses the same `(d, e)` → share `s2 = d + rho_B·e + c_B·x`.
4. With public `rho_A, rho_B` (recomputable from public preprocesses) and public `s1, s2`, an observer solves for `e`, then `d`; if `c_A != c_B` (different message), `x = ((s1 − s2) − (rho_A − rho_B)·e) / (c_A − c_B)`, recovering the validator's MuSig private key.

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

**File:** coordinator/src/tributary/signing_protocol.rs (L123-148)
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
  }
```

**File:** coordinator/src/tributary/signing_protocol.rs (L150-180)
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
```

**File:** coordinator/src/tributary/signing_protocol.rs (L231-249)
```rust
  let mut sorted = vec![];
  let mut threshold_is = map.keys().copied().collect::<Vec<_>>();
  threshold_is.sort();
  for threshold_i in threshold_is {
    sorted.push((key_from_threshold_i(threshold_i), map.remove(&threshold_i).unwrap()));
  }

  // Now that signers are sorted, with their shares, create a map with the is needed for MuSig
  let mut participants = vec![];
  let mut map = HashMap::new();
  for (raw_i, (key, share)) in sorted.into_iter().enumerate() {
    let musig_i = u16::try_from(raw_i).unwrap() + 1;
    participants.push(key);
    map.insert(Participant::new(musig_i).unwrap(), share);
  }

  map.remove(&our_threshold_i).unwrap();

  (participants, map)
```

**File:** crypto/frost/src/sign.rs (L361-398)
```rust
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
```

**File:** coordinator/src/tributary/handle.rs (L47-60)
```rust
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
}
```
