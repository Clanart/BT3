### Title
Panic on out-of-range participant indexes in PedPoP blame path causes node crash (DoS) - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The PedPoP blame-resolution code dereferences `HashMap`s keyed by `Participant` with attacker-influenced indexes without validating they are within `1 ..= n`. A `Participant` is any nonzero `u16`, so an accuser/accused index larger than `n` causes an unconditional panic (`HashMap` index on missing key) inside `Decryption::decrypt_with_proof` and `BlameMachine::blame_internal`, crashing the process handling the blame — the same bug class as CVE-2016-10068 (crafted input → application crash).

### Finding Description
`Participant::new` accepts any nonzero `u16` (`crypto/dkg/src/lib.rs` ~line 600 shows only the zero check). Neither `BlameMachine::blame` / `AdditionalBlameMachine::blame` nor `blame_internal` validate that `sender` and `recipient` are `<= params.n()`:

- `blame_internal` calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)`.
- Inside `decrypt_with_proof`, `self.enc_keys[&decryptor]` (`crypto/dkg/pedpop/src/encryption.rs:388`) panics if `decryptor` was never registered — e.g., a participant index `> n`, or the local participant's own index (only *other* participants' encryption keys are registered via `Decryption::register` in `generate_secret_shares`).
- If `decrypt_with_proof` succeeds (a valid `EncryptionKeyProof` can be produced by any holder of an encryption key), `self.commitments[&sender]` (`crypto/dkg/pedpop/src/lib.rs:599`) panics for any `sender` index not present in the commitments map.

Note that the well-formed protocol path does validate inputs elsewhere: `calculate_share` calls `validate_map` (`crypto/dkg/pedpop/src/lib.rs:468-472`) and `view` validates `included` (`crypto/dkg/src/lib.rs:463-491`). The blame path is the exception — it assumes the indexes are sane because they were "authenticated" by the caller, but the accused/accuser identities are attacker-chosen fields in a blame message.

### Impact Explanation
A panic here aborts key generation / slashing adjudication for the processing node. Any participant (or relayed accusation) can crash every node that evaluates the blame, preventing completion of the DKG and fault attribution — a remote denial of service directly analogous to the crafted-XML segfault in CVE-2016-10068. In a validator/processor context an unprivileged party who can submit a blame accusation naming `accused > n`, `accuser > n`, or `accused == local_i` forces a panic in honest nodes.

### Likelihood Explanation
Triggering requires only control of the `sender`/`recipient` arguments (or the corresponding accusation fields upstream) — no valid shares, proofs, or collusion needed for the `enc_keys[&decryptor]` panic. Any out-of-range index suffices; the only mitigation is that the caller could pre-validate, which the API neither does nor documents as a requirement.

### Recommendation
Validate `sender` and `recipient` against `self.encryption.enc_keys`/`self.commitments` (or `params.all_participant_indexes()`) in `blame_internal` before indexing; return a defined error/blame result instead of panicking. Replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `.get()` + error propagation.

### Proof of Concept
```rust
// n = 5 participants; machine completed calculate_share into BlameMachine
// An accusation naming a non-existent participant index:
let evil = Participant::new(250).unwrap(); // > n, but a valid nonzero u16
// This panics inside decrypt_with_proof at enc_keys[&decryptor],
// or at commitments[&sender] if decryption succeeds:
blame_machine.blame(evil, honest_participant, msg, proof);
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-397)
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
  }
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
