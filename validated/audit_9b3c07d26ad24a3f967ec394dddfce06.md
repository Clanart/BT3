### Title
Swapped accuser/accused in `VerifyBlame` causes blame to be attributed to the wrong party — an incorrect attribution/authorization check that punishes honest accusers and exonerates malicious share senders - ([File: processor/src/key_gen.rs](processor/src/key_gen.rs))

### Summary
The GitLab bug class is "incorrect authorization": an authenticated user performing an action on a namespace they do not own because the identity check targets the wrong entity. The PedPoP analog lives in `CoordinatorMessage::VerifyBlame` handling, where the processor verifies a blame accusation. `BlameMachine::blame` / `blame_internal` take `(sender, recipient)` — the sender being the party whose encrypted share is being adjudicated (i.e., the accused), and the recipient being the accusing decryptor. The processor invokes it with the arguments reversed, so the "authorization" of the blame claim is checked against the wrong participant.

### Finding Description
In `crypto/dkg/pedpop/src/lib.rs`, `BlameMachine::blame` is defined as:

```rust
pub fn blame(
  self,
  sender: Participant,
  recipient: Participant,
  msg: EncryptedMessage<C, SecretShare<C::F>>,
  proof: Option<EncryptionKeyProof<C>>,
) -> (AdditionalBlameMachine<C>, Participant)
```

Inside `blame_internal`, `sender` is used as `from` in the PoP verification (`pop_challenge::<C>(..., from, ...)`) and as the index into `self.commitments[&sender]`; `recipient` is used to look up the decryptor's registered encryption key `self.enc_keys[&decryptor]` and as the evaluation point in `share_verification_statements`. The tests confirm the convention: `machine.blame(ONE, TWO, msg, blame)` blames `ONE`, the party that sent the malformed share to `TWO` (the accuser).

In `processor/src/key_gen.rs` (`CoordinatorMessage::VerifyBlame { id, accuser, accused, share, blame }`), however, the call is:

```rust
.blame(accuser, accused, substrate_share, substrate_blame);
// and
.blame(accuser, accused, network_share, network_blame);
```

`accuser` (the party claiming the share was invalid) is passed as `sender`, and `accused` (the party who actually sent the share) is passed as `recipient`. Both roles are bound to the wrong participant:

1. The PoP inside the `EncryptedMessage` was signed with `from = accused` (the real sender) in `pop_challenge`. `decrypt_with_proof` recomputes the challenge with `from = accuser`, so `msg.pop.verify` fails for every legitimately-formed message, returning `DecryptionError::InvalidSignature` and causing `blame_internal` to return `sender` — i.e., the accuser.
2. Even if a caller crafted a PoP matching `from = accuser`, the DLEq check uses `self.enc_keys[&decryptor]` with `decryptor = accused`, i.e., the sender's own encryption key, which the accuser cannot produce a valid ECDH proof for — `InvalidProof` again returns `sender = accuser`.

The processor then concludes:

```rust
if (substrate_blame == accused) || (network_blame == accused) {
  return ProcessorMessage::Blame { id, participant: accused };
}
ProcessorMessage::Blame { id, participant: accuser }
```

Since `blame_internal` can only ever return `accuser` (as `sender`) under this wiring, every `VerifyBlame` deterministically returns `Blame { participant: accuser }`.

### Impact Explanation
- A malicious DKG participant who distributes an invalid/non-decryptable secret share can never be blamed: any honest participant that raises the accusation is themself marked at fault and slashed.
- An attacker who did *not* misbehave can also weaponize this: anyone can submit a `VerifyBlame` naming an honest participant as `accuser`, and the verdict will blame that honest party regardless of the message contents (a fabricated `share`/`blame` pair still lands on `InvalidSignature`/`InvalidProof` → `sender` = `accuser`).
- Fault attribution is a consensus/slashing input; blaming the wrong party aborts key generation and penalizes honest validators while shielding the actual faulty one — a concrete integrity failure of the same shape as the GitLab issue (the security check is applied to the wrong identity).

### Likelihood Explanation
Reachable via unprivileged protocol inputs: the DKG share message and the `VerifyBlame` coordinator message are attacker-influenceable data (`EncryptedMessage::read`/`EncryptionKeyProof::read` on supplied bytes). No collusion, leaked keys, or malicious-node assumptions are needed — the argument swap produces the wrong verdict on every invocation. The one caveat is that triggering `VerifyBlame` requires the coordinator to accept the blame transaction, which is normal protocol flow, not an exotic precondition.

### Recommendation
Swap the arguments in `processor/src/key_gen.rs` so the accused (the share's actual sender) is `sender` and the accuser is `recipient`:

```rust
.blame(accused, accuser, substrate_share, substrate_blame);
.blame(accused, accuser, network_share, network_blame);
```

Additionally, rename the `blame` parameters to `(accused, accuser, ...)` or document the ordering at the `BlameMachine`/`AdditionalBlameMachine` API to prevent recurrence, and add a processor-level test where a known-malicious sender is successfully blamed.

### Proof of Concept
Two validators run PedPoP. Participant `A` (index 1) sends participant `B` (index 2) an `EncryptedMessage` whose ciphertext does not decode to a scalar (equivalent to the existing `invalid_share_serialization_blame` test). `B` calls `calculate_share`, obtains `PedPoPError::InvalidShare { participant: A, blame: Some(proof) }`, and submits `VerifyBlame { accuser: B, accused: A, share, blame }`.

The processor executes `.blame(B, A, share, proof)`:

- `decrypt_with_proof(sender=B, decryptor=A, ...)` verifies `msg.pop` against `pop_challenge(..., from=B, ...)`. The ciphertext's PoP was computed with `from=A`, so verification fails → `InvalidSignature` → `blame_internal` returns `B` (= `sender` = `accuser`).
- Both `substrate_blame` and `network_blame` equal `B`, so neither equals `accused` (`A`), and the processor emits `ProcessorMessage::Blame { id, participant: B }` — the honest accuser is blamed and `A` escapes.

The correct call `.blame(A, B, share, proof)` would reach `share_verification_statements(recipient=B, &commitments[&A], share)` and blame `A`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** processor/src/key_gen.rs (L543-563)
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

        // If the accused was blamed for either, mark them as at fault
        if (substrate_blame == accused) || (network_blame == accused) {
          return ProcessorMessage::Blame { id, participant: accused };
        }

        ProcessorMessage::Blame { id, participant: accuser }
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L302-324)
```rust
fn pop_challenge<C: Ciphersuite>(
  context: [u8; 32],
  nonce: C::G,
  key: C::G,
  sender: Participant,
  msg: &[u8],
) -> C::F {
  let mut transcript = RecommendedTranscript::new(b"DKG Encryption Key Proof of Possession v0.2");
  transcript.append_message(b"context", context);

  transcript.domain_separate(b"proof_of_possession");

  transcript.append_message(b"nonce", nonce.to_bytes());
  transcript.append_message(b"key", key.to_bytes());
  // This is sufficient to prevent the attack this is meant to stop
  transcript.append_message(b"sender", sender.to_bytes());
  // This, as written above, doesn't hurt
  transcript.append_message(b"message", msg);
  // While this is a PoK and a PoP, it's called a PoP here since the important part is its owner
  // Elsewhere, where we use the term PoK, the important part is that it isn't some inverse, with
  // an unknown to anyone discrete log, breaking the system
  C::hash_to_F(b"DKG-encryption-proof_of_possession", &transcript.challenge(b"schnorr"))
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L366-397)
```rust
  pub(crate) fn decrypt_with_proof<E: Encryptable>(
    &self,
    from: Participant,
    decryptor: Participant,
    mut msg: EncryptedMessage<C, E>,
    // There's no encryption key proof if the accusation is of an invalid signature
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Result<Zeroizing<E>, DecryptionError> {
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }

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
  }
```

**File:** crypto/dkg/pedpop/src/tests.rs (L149-172)
```rust
fn test_blame(
  commitment_msgs: &HashMap<Participant, EncryptionKeyMessage<Ristretto, Commitments<Ristretto>>>,
  machines: Vec<BlameMachine<Ristretto>>,
  msg: &PedPoPEncryptedMessage<Ristretto>,
  blame: &Option<EncryptionKeyProof<Ristretto>>,
) {
  for machine in machines {
    let (additional, blamed) = machine.blame(ONE, TWO, msg.clone(), blame.clone());
    assert_eq!(blamed, ONE);
    // Verify additional blame also works
    assert_eq!(additional.blame(ONE, TWO, msg.clone(), blame.clone()), ONE);

    // Verify machines constructed with AdditionalBlameMachine::new work
    assert_eq!(
      AdditionalBlameMachine::new(CONTEXT, PARTICIPANTS, commitment_msgs.clone()).unwrap().blame(
        ONE,
        TWO,
        msg.clone(),
        blame.clone()
      ),
      ONE,
    );
  }
}
```
