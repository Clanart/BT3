### Title
Unauthenticated blame accusations let any party frame an honest DKG recipient as faulty - ([File: crypto/dkg/pedpop/src/lib.rs](crypto/dkg/pedpop/src/lib.rs))

### Summary

Analogous to CVE-2019-12431 (a restricted party gaining access to resources they were not entitled to via a gap in authorization), PedPoP's blame adjudication path never verifies that the accusing party is actually the recipient of the accused message. `AdditionalBlameMachine::blame` / `BlameMachine::blame` accept a caller-supplied `(sender, recipient)` pair plus the ciphertext and an optional `EncryptionKeyProof`, and `Decryption::decrypt_with_proof` treats a missing/invalid proof as proof that *the recipient* lied — returning `recipient` as the faulty party. A third party (or any participant) can therefore take any honestly generated `EncryptedMessage` from sender S to victim R, file an accusation `(S, R, msg, None)`, and have the honest R adjudicated faulty — aborting the DKG and attributing fault to an innocent party who never claimed anything.

### Finding Description

`blame` delegates to `blame_internal`, which calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` [1](#0-0) . Inside `decrypt_with_proof` [2](#0-1) :

1. The Schnorr PoP is verified, binding the message to `from` (the sender) — but nothing binds the accusation to `recipient`.
2. If `proof` is `None` (or fails the DLEq check against `self.enc_keys[&decryptor]`), it returns `Err(DecryptionError::InvalidProof)`, and `blame_internal` maps that to `recipient` being faulty [3](#0-2) .

The doc comments only require that "the message ... must have been authenticated as actually having come from the sender" — there is no requirement that the accusation originate from, or be authorized by, the named recipient [4](#0-3) . `AdditionalBlameMachine::new` is explicitly usable "regardless of if the caller was a member in the DKG protocol" [5](#0-4) , so the adjudication API is open to non-participants.

In the legitimate protocol flow, `proof` is only `None` when the PoP itself was invalid (`PedPoPError::InvalidShare { .., blame: None }`) [6](#0-5) . For a message with a *valid* PoP, an honest recipient would always have produced a valid `EncryptionKeyProof` (it is generated unconditionally during `decrypt` [7](#0-6) ). So a `(valid PoP, proof = None)` accusation can only arise from a fabricated accusation — exactly the case the code mishandles by blaming the recipient.

### Impact Explanation

An attacker who observes the public DKG transcript can cause any honest participant R to be declared the faulty party in a blame adjudication, without R having sent anything. Concretely this:

- aborts the key generation attempt for all participants, and
- attributes fault to an innocent party — in Serai's processor flow (`CoordinatorMessage::VerifyBlame`), a blame result is escalated as `ProcessorMessage::Blame { participant: accuser/accused }`, i.e. grounds for slashing the wrongly-accused validator.

It is an integrity/availability failure reachable purely from public protocol messages (`EncryptedMessage` values are broadcast artifacts).

### Likelihood Explanation

The attack requires only a legitimately transmitted `EncryptedMessage` from some sender S to victim R — all of which are public protocol traffic — plus the ability to submit a blame accusation naming R as recipient. No secret keys, no valid decryption proof, and no collusion are needed. The cost is one blame transaction/message. The main mitigating factor is that the surrounding processor may authenticate the `accuser` field, which bounds the identity an attacker can claim — but `blame` itself performs no such binding and the library documentation does not state it as a caller obligation, so any deployment that adjudicates accusations without re-verifying that the accuser authored the complaint against *their own* incoming share is exposed.

### Recommendation

In `blame` / `blame_internal` (and in `Decryption::decrypt_with_proof`), require proof that the accusation originates from the named `recipient` before attributing fault:

- Require a non-`None` `EncryptionKeyProof` for any accusation over a message whose PoP is valid — a valid-PoP + `None`-proof accusation should resolve to the *accuser*/unknown being at fault (or be rejected as malformed), never the recipient.
- Authenticate the accusation itself (e.g., require a signature from `recipient` over `(sender, msg_hash, context)`), or document that callers MUST verify the accusation was issued by the named recipient.
- Update `crypto/dkg/pedpop/src/tests.rs` with a test asserting `blame(S, R, honest_msg, None)` does not return `R`.

### Proof of Concept

```rust
// Pseudo-PoC against crypto/dkg/pedpop
// 1. Run an honest PedPoP key gen among participants 1..=n.
//    Let msg = the EncryptedMessage<C, SecretShare> honestly sent by S to R
//    (valid PoP, valid encryption, valid share).

// 2. A third party (not S, not R, not even a participant) constructs an
//    adjudicator over the public commitment messages:
let machine = AdditionalBlameMachine::<C>::new(context, n, commitment_msgs).unwrap();

// 3. File a fabricated accusation naming honest R as the recipient,
//    with no EncryptionKeyProof:
let blamed = machine.blame(S, R, msg.clone(), None);

// Inside blame_internal:
//   - decrypt_with_proof verifies the PoP: OK (msg is genuine)
//   - proof is None -> Err(DecryptionError::InvalidProof)
//   - blame_internal returns `recipient` => R
assert_eq!(blamed, R); // honest R declared faulty; DKG aborts, R may be slashed
```

`blame_internal` in `crypto/dkg/pedpop/src/lib.rs:575-609` and `decrypt_with_proof` in `crypto/dkg/pedpop/src/encryption.rs:366-397` contain the relevant logic.

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L493-499)
```rust
    batch.verify_with_vartime_blame().map_err(|id| {
      let (l, blame) = match id {
        BatchId::Decryption(l) => (l, None),
        BatchId::Share(l) => (l, Some(blames.remove(&l).unwrap())),
      };
      PedPoPError::InvalidShare { participant: l, blame }
    })?;
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

**File:** crypto/dkg/pedpop/src/lib.rs (L611-622)
```rust
  /// Given an accusation of fault, determine the faulty party (either the sender, who sent an
  /// invalid secret share, or the receiver, who claimed a valid secret share was invalid). No
  /// matter which, prevent completion of the machine, forcing an abort of the protocol.
  ///
  /// The message should be a copy of the encrypted secret share from the accused sender to the
  /// accusing recipient. This message must have been authenticated as actually having come from
  /// the sender in question.
  ///
  /// In order to enable detecting multiple faults, an `AdditionalBlameMachine` is returned, which
  /// can be used to determine further blame. These machines will process the same blame statements
  /// multiple times, always identifying blame. It is the caller's job to ensure they're unique in
  /// order to prevent multiple instances of blame over a single incident.
```

**File:** crypto/dkg/pedpop/src/lib.rs (L639-648)
```rust
  /// Create an AdditionalBlameMachine capable of evaluating Blame regardless of if the caller was
  /// a member in the DKG protocol.
  ///
  /// Takes in the parameters for the DKG protocol and all of the participant's commitment
  /// messages.
  ///
  /// This constructor assumes the full validity of the commitment messages. They must be fully
  /// authenticated as having come from the supposed party and verified as valid. Usage of invalid
  /// commitments is considered undefined behavior, and may cause everything from inaccurate blame
  /// to panics.
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L487-500)
```rust
    let key = ecdh::<C>(&self.enc_key, msg.key);
    cipher::<C>(self.context, &key).apply_keystream(msg.msg.as_mut().as_mut());
    (
      msg.msg,
      EncryptionKeyProof {
        key,
        dleq: DLEqProof::prove(
          rng,
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &self.enc_key,
        ),
      },
    )
```
