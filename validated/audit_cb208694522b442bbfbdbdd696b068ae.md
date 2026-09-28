### Title
Unbounded `sender`/`recipient` participant index in PedPoP blame evaluation panics on out-of-range key lookup, crashing every node that evaluates the accusation - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` accept attacker-controlled `sender` and `recipient` `Participant` values and index `self.commitments[&sender]` and `self.enc_keys[&decryptor]` without any bounds check. A malicious accuser supplies `sender` = a valid non-zero `Participant` greater than `n` (e.g. `Participant(0xffff)`); the shared `blame_internal` path then panics on a `HashMap` index into `commitments`, which only contains participants `1..=n`. This is the direct analog of CVE-2018-20360: an invalid "address" (map key) dereference over attacker-influenced data producing a crash (denial of service).

### Finding Description
In `blame_internal`, after the encrypted share message decrypts to a canonical scalar, the share is verified against the accused sender's commitments via an unchecked index: [1](#0-0) 

`self.commitments` is populated only for real participants (`1..=n`) in `calculate_share` / `AdditionalBlameMachine::new`: [2](#0-1) 

`Participant` only enforces non-zero (`Participant::new` rejects 0; coordinator deserialization of `InvalidDkgShare` performs only this check on `accuser`/`faulty`), so any value `> n` passes parsing. Neither `blame` nor `blame_internal` validates `sender <= n` or `recipient <= n` before indexing: [3](#0-2) 

Additionally, `decrypt_with_proof` indexes `self.enc_keys[&decryptor]` where `enc_keys` omits the node's own index (self-registration is skipped in `verify_r1`), so an accusation with `recipient == self.i` on a participant's own `BlameMachine` also panics: [4](#0-3) [5](#0-4) 

### Impact Explanation
Any participant can file a blame/`InvalidDkgShare` accusation naming a non-existent `faulty`/`sender` index. Every node that evaluates the accusation — both the accused-side `BlameMachine` and observer nodes using `AdditionalBlameMachine::blame`, which delegates to the same `blame_internal` — hits the out-of-range `HashMap` index and panics, taking down the process mid-DKG/reshare. This aborts key generation across the validator set (a consensus-relevant DoS), matching the Medium-severity crash-only profile of the reference CVE.

### Likelihood Explanation
Reaching the panic requires the supplied `msg`/`proof` to survive `decrypt_with_proof`: the PoP Schnorr must verify (attacker crafts `msg.key`/PoP for arbitrary `from`), and the accuser supplies an `EncryptionKeyProof` with a valid DLEq under their own known encryption scalar, plus a ciphertext decrypting to a canonical scalar — all constructible by a malicious accuser with public messages. The only barrier is being a DKG participant able to file an accusation, which is within the unprivileged-party scope (messages they cause to be processed).

### Recommendation
Validate `sender` and `recipient` in `blame`/`blame_internal` (and `AdditionalBlameMachine::new`'s inputs) against `params.n()` and the presence of their `commitments`/`enc_keys` entries, returning a `PedPoPError` (e.g. `MissingParticipant`/a new invalid-participant variant) instead of indexing. Replace `self.commitments[&sender]` and `self.enc_keys[&decryptor]` with `.get()` + error propagation, and give `BlameMachine`/`Decryption` awareness of `params.n()`.

### Proof of Concept
```rust
// victim node has completed calculate_share for params (t, n)
// attacker (a participant) calls, or causes honest nodes to call:
let sender = Participant::new(n + 100).unwrap();   // > n, non-zero: parses fine
let recipient = accuser_index;                     // real participant with registered enc key

// attacker crafts:
//  msg.key = k*G for chosen k; pop = SchnorrSignature::sign(k, ..., pop_challenge(ctx, R, k*G, sender, ct))
//  ct   = ChaCha20 keystream applied to a canonical F::Repr under key = x_acc * msg.key
//  proof.key = x_acc * msg.key; proof.dleq = DLEqProof over [G, msg.key] -> [enc_pub_acc, proof.key]
// pop.verify passes; dleq.verify passes; decrypted bytes parse as F.
// Then in blame_internal:
//   share_verification_statements(recipient, &self.commitments[&sender], ...)
//   => index panic: no entry found for key Participant(n + 100)
let _faulty = blame_machine.blame(sender, recipient, forged_msg, Some(forged_proof));
```

Uncertain: whether the coordinator/processor performs its own range check on `accuser`/`faulty` before invoking `blame` — the tributary deserialization checks only non-zero `Participant` values, and no check exists inside the PedPoP API itself.

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L311-337)
```rust
    let mut batch = BatchVerifier::<Participant, C::G>::new(commitment_msgs.len());
    let mut commitments = HashMap::new();
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);

      if msg.commitments.len() != self.params.t().into() {
        Err(PedPoPError::InvalidCommitments(l))?;
      }

      // Step 5: Validate each proof of knowledge
      // This is solely the prep step for the latter batch verification
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );

      commitments.insert(l, msg.commitments.drain(..).collect::<Vec<_>>());
    }

    batch.verify_vartime_with_vartime_blame().map_err(PedPoPError::InvalidCommitments)?;

    commitments.insert(self.params.i(), self.our_commitments.drain(..).collect());
    Ok(commitments)
```

**File:** crypto/dkg/pedpop/src/lib.rs (L575-609)
```rust
  fn blame_internal(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) {
      Ok(share_bytes) => share_bytes,
      // If there's an invalid signature, the sender did not send a properly formed message
      Err(DecryptionError::InvalidSignature) => return sender,
      // Decryption will fail if the provided ECDH key wasn't correct for the given message
      Err(DecryptionError::InvalidProof) => return recipient,
    };

    let Some(share) = Option::<C::F>::from(C::F::from_repr(share_bytes.0)) else {
      // If this isn't a valid scalar, the sender is faulty
      return sender;
    };

    // If this isn't a valid share, the sender is faulty
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
      return sender;
    }

    // The share was canonical and valid
    recipient
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L654-661)
```rust
    let mut commitments = HashMap::new();
    let mut encryption = Decryption::new(context);
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
    Ok(AdditionalBlameMachine(BlameMachine { commitments, encryption, result: None }))
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-396)
```rust
    if let Some(proof) = proof {
      // Verify this is the decryption key for this message
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;

      cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg.as_mut().as_mut());
      Ok(msg.msg)
    } else {
      Err(DecryptionError::InvalidProof)
    }
```
