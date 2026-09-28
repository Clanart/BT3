### Title
Blame adjudication uses swapped sender/recipient arguments, letting one DKG participant get any honest participant blamed and removed — (File: processor/src/key_gen.rs, crypto/dkg/pedpop/src/lib.rs)

### Summary
The PedPoP blame protocol is Serai's analog of the CVE-2020-36251 class: a party with only ordinary (non-administrative) membership in the group protocol can cause another member to be stripped of their access. `AdditionalBlameMachine::blame` is documented and implemented as `blame(sender, recipient, msg, proof)`, where `sender` is the accused share-sender and `recipient` is the accusing party [1](#0-0) . The processor calls it as `.blame(accuser, accused, substrate_share, substrate_blame)` [2](#0-1) , passing the accuser as `sender` and the accused as `recipient`. Combined with the coordinator fetching the stored share with `DkgShare::get(self.txn, genesis, accuser.into(), faulty.into())` — keyed `(from, to)` as established by `DkgShare::set(..., from.into(), to.into(), share)` [3](#0-2) [4](#0-3)  — the machine evaluates the accuser's *own outgoing* share message, which always has a valid PoP, and then attributes the unavoidable key-proof failure to `recipient`, i.e. the accused.

### Finding Description
In `blame_internal`, the outcomes are: `InvalidSignature` → blame `sender`; `InvalidProof` → blame `recipient`; non-scalar/invalid share → blame `sender`; valid share → blame `recipient` [5](#0-4) . Because the processor passes `(accuser, accused)` as `(sender, recipient)`:

1. The message retrieved from `DkgShare::get(accuser, faulty)` is the share the accuser sent *to* the accused (key order `(from, to)`), so its Schnorr PoP was legitimately signed with `from = accuser` and `pop_challenge` is computed with `from = accuser` — the signature check passes [6](#0-5) .
2. The DLEq check requires `proof.key = msg.key^{d}` where `d` is `enc_keys[decryptor = accused]` — the *accused's* encryption key [7](#0-6) . The accuser does not know `d` and cannot satisfy this; with `blame = None` (an "invalid signature" accusation) the code takes `Err(DecryptionError::InvalidProof)` unconditionally [8](#0-7) .
3. `InvalidProof` returns `recipient`, which due to the swap is the **accused** [9](#0-8) .
4. `VerifyBlame` therefore emits `ProcessorMessage::Blame { participant: accused }` [10](#0-9) , which every honest processor reproduces deterministically, producing `RemoveParticipantDueToDkg` votes [11](#0-10)  that accumulate to `t` and trigger `fatal_slash` of the innocent accused [12](#0-11) .

An accusation with `blame: None` suffices — the accuser needs no forged proof, only an ordinary DKG share-sending position (equivalent to the CVE's "received non-administrative access to a group share").

### Impact Explanation
A single DKG participant can unilaterally cause an innocent participant to be blamed, voted for removal, and fatally slashed — revoking that member's access to the threshold key/share for the entire group, mirroring the CVE's "remove everyone else's access to that share" primitive. Repeated across attempts (`InvalidDkgShare` is only gated on the accuser being in-range and `faulty` being a real counterparty [13](#0-12) ), one malicious member can eject honest members up to the threshold-removal limit, destroying honest validators' stake and degrading the group's participant set.

### Likelihood Explanation
High reachability: the attacker only needs to be a normal DKG participant publishing an `InvalidDkgShare` transaction naming an arbitrary `faulty` participant with empty blame. No collusion, no proof forgery, and no invalid cryptography is required — the wrong-party verdict is produced deterministically by the swapped arguments and the `(from, to)` share lookup, so all honest validators converge on slashing the victim.

### Recommendation
In `processor/src/key_gen.rs` `VerifyBlame`, call `.blame(accused, accuser, share, blame)` — accused as `sender`, accuser as `recipient` — and have the coordinator fetch `DkgShare::get(txn, genesis, faulty, accuser)` (the share sent *from* the accused *to* the accuser). Add a test where a false `InvalidDkgShare` accusation results in the *accuser* being blamed, not the accused.

### Proof of Concept
1. Honest validators run a PedPoP DKG; participant A publishes `DkgShares`, including a correctly encrypted share A→B stored under `DkgShare(from=A, to=B)`.
2. A publishes `Transaction::InvalidDkgShare { accuser: A, faulty: B, blame: None }`.
3. Coordinator loads `DkgShare::get(accuser=A, faulty=B)` → the A→B message, whose PoP verifies under `from = A` (the `sender` argument).
4. `VerifyBlame` calls `blame(sender=A, recipient=B, …, proof=None)` → `decrypt_with_proof` returns `InvalidProof` → `blame_internal` returns `recipient` = B.
5. `ProcessorMessage::Blame { participant: B }` → `RemoveParticipantDueToDkg` → B reaches `t` votes and is fatally slashed despite having done nothing wrong.

Caveat: this conclusion assumes the `DkgShare` DB is keyed `(from, to)` matching the `set` call site; I could not read `db.rs` key definitions in the remaining iterations. If it were keyed `(to, from)`, the same swap instead breaks blame in the opposite direction (honest accusers get blamed via `InvalidSignature` → `sender` = accuser), which is still the same swapped-argument defect with a denial-of-justice impact rather than wrongful removal.

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L582-608)
```rust
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L623-632)
```rust
  pub fn blame(
    self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> (AdditionalBlameMachine<C>, Participant) {
    let faulty = self.blame_internal(sender, recipient, msg, proof);
    (AdditionalBlameMachine(self), faulty)
  }
```

**File:** processor/src/key_gen.rs (L543-556)
```rust
        let substrate_blame = AdditionalBlameMachine::new(
          context(&id, SUBSTRATE_KEY_CONTEXT),
          params.n(),
          substrate_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, substrate_share, substrate_blame);
        let network_blame = AdditionalBlameMachine::new(
          context(&id, NETWORK_KEY_CONTEXT),
          params.n(),
          network_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, network_share, network_blame);
```

**File:** processor/src/key_gen.rs (L558-563)
```rust
        // If the accused was blamed for either, mark them as at fault
        if (substrate_blame == accused) || (network_blame == accused) {
          return ProcessorMessage::Blame { id, participant: accused };
        }

        ProcessorMessage::Blame { id, participant: accuser }
```

**File:** coordinator/src/tributary/handle.rs (L274-283)
```rust
        VotedToRemove::set(self.txn, genesis, signer, participant, &());

        let prior_votes = VotesToRemove::get(self.txn, genesis, participant).unwrap_or(0);
        let signer_votes =
          self.spec.i(&[], signed.signer).expect("signer wasn't a validator for this network?");
        let new_votes = prior_votes + u16::from(signer_votes.end) - u16::from(signer_votes.start);
        VotesToRemove::set(self.txn, genesis, participant, &new_votes);
        if ((prior_votes + 1) ..= new_votes).contains(&self.spec.t()) {
          self.fatal_slash(participant, "RemoveParticipantDueToDkg vote")
        }
```

**File:** coordinator/src/tributary/handle.rs (L347-362)
```rust
        for (from_offset, shares) in shares.iter().enumerate() {
          let from =
            Participant::new(u16::from(sender_i.start) + u16::try_from(from_offset).unwrap())
              .unwrap();

          for (to_offset, share) in shares.iter().enumerate() {
            // 0-indexed (the enumeration) to 1-indexed (Participant)
            let mut to = u16::try_from(to_offset).unwrap() + 1;
            // Adjust for the omission of the sender's own shares
            if to >= u16::from(sender_i.start) {
              to += u16::from(sender_i.end) - u16::from(sender_i.start);
            }
            let to = Participant::new(to).unwrap();

            DkgShare::set(self.txn, genesis, from.into(), to.into(), share);
          }
```

**File:** coordinator/src/tributary/handle.rs (L461-492)
```rust
      Transaction::InvalidDkgShare { attempt, accuser, faulty, blame, signed } => {
        let Some(removed) = removed_as_of_dkg_attempt(self.txn, genesis, attempt) else {
          self
            .fatal_slash(signed.signer.to_bytes(), "InvalidDkgShare with an unrecognized attempt");
          return;
        };
        let Some(range) = self.spec.i(&removed, signed.signer) else {
          self.fatal_slash(
            signed.signer.to_bytes(),
            "InvalidDkgShare for a DKG they aren't participating in",
          );
          return;
        };
        if !range.contains(&accuser) {
          self.fatal_slash(
            signed.signer.to_bytes(),
            "accused with a Participant index which wasn't theirs",
          );
          return;
        }
        if range.contains(&faulty) {
          self.fatal_slash(signed.signer.to_bytes(), "accused self of having an InvalidDkgShare");
          return;
        }

        let Some(share) = DkgShare::get(self.txn, genesis, accuser.into(), faulty.into()) else {
          self.fatal_slash(
            signed.signer.to_bytes(),
            "InvalidDkgShare had a non-existent faulty participant",
          );
          return;
        };
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-379)
```rust
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-390)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L394-396)
```rust
    } else {
      Err(DecryptionError::InvalidProof)
    }
```

**File:** coordinator/src/main.rs (L537-549)
```rust
        key_gen::ProcessorMessage::Blame { id, participant } => {
          let participant = spec
            .reverse_lookup_i(
              &crate::tributary::removed_as_of_dkg_attempt(&txn, spec.genesis(), id.attempt)
                .expect("participating in DKG attempt yet we didn't save who was removed"),
              participant,
            )
            .unwrap();
          vec![Transaction::RemoveParticipantDueToDkg {
            participant,
            signed: Transaction::empty_signed(),
          }]
        }
```
