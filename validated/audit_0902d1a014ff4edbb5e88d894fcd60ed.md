### Title
Unchecked map lookups in PedPoP blame handling allow an unprivileged participant to panic any validator via a malformed blame accusation - (File: crypto/dkg/pedpop/src/lib.rs, crypto/dkg/pedpop/src/encryption.rs)

### Summary
The kernel bug class (CVE-2023-53005) is a helper's failure return being consumed unchecked, yielding a null-pointer dereference. The Rust analog in Serai is unchecked `HashMap` indexing/`unwrap()` on values derived from attacker-controlled inputs in the PedPoP blame path. `BlameMachine::blame` / `AdditionalBlameMachine::blame` accept `sender` and `recipient` `Participant` values plus an attacker-supplied `EncryptedMessage`, and pass them into `blame_internal` and `decrypt_with_proof`, which index `self.commitments[&sender]` and `self.enc_keys[&decryptor]` without checking membership.

### Finding Description
`AdditionalBlameMachine::new` registers encryption keys and commitments only for participants `1..=n` (`crypto/dkg/pedpop/src/lib.rs:656-661`). Yet `blame()` accepts any nonzero `Participant` for `sender`/`recipient` (`crypto/dkg/pedpop/src/lib.rs:674-682`) and forwards them to `blame_internal`, which does `self.commitments[&sender]` (`lib.rs:600`) — panicking if `sender` was not a DKG participant. Independently, `Decryption::decrypt_with_proof` does `self.enc_keys[&decryptor]` (`crypto/dkg/pedpop/src/encryption.rs:388`) — panicking if `recipient` isn't a registered participant, regardless of message validity. Neither `sender`/`recipient` is validated against `n` or against the commitment set anywhere in `blame`, `blame_internal`, or `decrypt_with_proof`. Compare with `decrypt_with_proof`'s own `Option`-based error handling for invalid proofs — the missing-key case is simply not handled, exactly like the unchecked `create_hist_field` return.

### Impact Explanation
A blame accusation naming an out-of-range participant (e.g., `Participant(n+1)` as `sender`, or any `recipient > n`) reaches a `HashMap` index on a missing key and panics the host validator. Since `AdditionalBlameMachine::new` is explicitly designed to let third parties evaluate blame over others' accusations (`lib.rs:639-661`), a node processing a peer's accusation can be crashed by a single malformed accusation message — an unprivileged remote denial of service against validators/observers evaluating blame.

### Likelihood Explanation
Triggering requires only that an accusation referencing an invalid participant index be submitted for blame evaluation — no valid keys, threshold cooperation, or cryptographic forgery is needed. The panic fires before any message authentication matters. Likelihood is moderate: it depends on integrators routing externally-originated (accuser, accused) pairs into `blame`, which is the documented purpose of `AdditionalBlameMachine`. `Participant::new` rejects zero but nothing bounds the value to `n`.

### Recommendation
Validate `sender` and `recipient` at the top of `blame`/`blame_internal` (e.g., `u16::from(sender) <= n` and membership in `commitments`), and replace `self.commitments[&sender]` / `self.enc_keys[&decryptor]` with `.get()` returning a defined fault result or error. Since `blame_internal` must always return a `Participant`, out-of-range indexes could be reported as the accuser being faulty, or the API changed to return `Option<Participant>`/`Result`.

### Proof of Concept
```rust
// Setup: an n-of-n DKG completes, or an observer builds:
//   AdditionalBlameMachine::new(context, n, commitment_msgs)
// with n = 3.
//
// Attacker submits an accusation where `sender` is a validly-formed
// Participant not in the DKG set:

let bogus_sender = Participant::new(4).unwrap(); // n == 3
let recipient    = Participant::new(1).unwrap();

// msg: any EncryptedMessage<SecretShare> bytes; validity is irrelevant.
// proof: None.

// BlameMachine::blame -> blame_internal ->
//   self.commitments[&bogus_sender]  // lib.rs:600 — panic: key not found

// Variant via AdditionalBlameMachine::blame with recipient = Participant(4):
//   decrypt_with_proof -> self.enc_keys[&decryptor]  // encryption.rs:388 — panic
```

Caveat: whether the panic is reachable depends on the caller passing externally-influenced `Participant` values into `blame` without pre-filtering; the library itself performs no bounds check, so the invariant is entirely the integrator's burden. Impact is availability-only (panic/abort), consistent with the Medium severity of the source advisory.

Sources:
- `blame` / `blame_internal` / `commitments[&sender]`: [1](#0-0) 
- `AdditionalBlameMachine::blame` / `new` registering only `1..=n`: [2](#0-1) 
- `decrypt_with_proof` `enc_keys[&decryptor]`: [3](#0-2) 
- `Decryption::register` (asserts no re-registration; no validation of later lookups): [4](#0-3)

### Citations

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

**File:** crypto/dkg/pedpop/src/lib.rs (L649-682)
```rust
  pub fn new(
    context: [u8; 32],
    n: u16,
    mut commitment_msgs: HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>>,
  ) -> Result<Self, PedPoPError<C>> {
    let mut commitments = HashMap::new();
    let mut encryption = Decryption::new(context);
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
    Ok(AdditionalBlameMachine(BlameMachine { commitments, encryption, result: None }))
  }

  /// Given an accusation of fault, determine the faulty party (either the sender, who sent an
  /// invalid secret share, or the receiver, who claimed a valid secret share was invalid).
  ///
  /// The message should be a copy of the encrypted secret share from the accused sender to the
  /// accusing recipient. This message must have been authenticated as actually having come from
  /// the sender in question.
  ///
  /// This will process the same blame statement multiple times, always identifying blame. It is
  /// the caller's job to ensure they're unique in order to prevent multiple instances of blame
  /// over a single incident.
  pub fn blame(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    self.0.blame_internal(sender, recipient, msg, proof)
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-361)
```rust
  pub(crate) fn register<M: Message>(
    &mut self,
    participant: Participant,
    msg: EncryptionKeyMessage<C, M>,
  ) -> M {
    assert!(
      !self.enc_keys.contains_key(&participant),
      "Re-registering encryption key for a participant"
    );
    self.enc_keys.insert(participant, msg.enc_key);
    msg.msg
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
