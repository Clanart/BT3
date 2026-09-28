### Title
Panic on out-of-range `Participant` indexes in PedPoP blame evaluation causes node crash - (File: crypto/dkg/pedpop/src/lib.rs, crypto/dkg/pedpop/src/encryption.rs)

### Summary
The analog of CVE-2023-0394 (a NULL dereference crashing the system on attacker-influenced input) is an unchecked-index panic reachable from peer-supplied participant identifiers. `BlameMachine::blame` / `AdditionalBlameMachine::blame` take `sender` and `recipient` as `Participant` values and index `self.commitments[&sender]` and `self.enc_keys[&decryptor]` with them. `Participant` is only a non-zero `u16` (`Participant::new` rejects solely `0`), so any value in `(n, u16::MAX]` is accepted by the type but is absent from both `HashMap`s, causing `HashMap` indexing to panic. This crashes the process instead of returning a blame verdict.

### Finding Description
Two panic sites are reachable:

1. `Decryption::decrypt_with_proof` evaluates `self.enc_keys[&decryptor]` inside the `if let Some(proof)` branch (encryption.rs:388). `decryptor` is the `recipient` argument passed by the caller to `blame`. A `recipient` index `> n` panics immediately.
2. If `recipient` is valid but `sender` is `> n`, `blame_internal` reaches `self.commitments[&sender]` (pedpop/src/lib.rs:599), which panics. Reaching it only requires a validly-formed `EncryptedMessage` and `EncryptionKeyProof`, both constructible from public data: the accuser picks `k`, sets `msg.key = G*k`, signs the PoP binding `sender` (the PoP challenge commits to `sender` but any value can be signed over — pop_challenge at encryption.rs:302-324), and sets `proof.key = enc_pub_key[recipient] * k`, satisfying the DLEq over `[G, msg.key] -> [enc_keys[recipient], proof.key]` (encryption.rs:383-390). `enc_pub_key` is broadcast in the public `EncryptionKeyMessage`, so no secret knowledge is required.

`Participant` carries no `<= n` bound (dkg/src/lib.rs:26-35), and neither `blame` entry point (pedpop/src/lib.rs:623-632, 674-682) nor `blame_internal` validates `sender`/`recipient` against the participant set before the indexing operations.

### Impact Explanation
Any party able to submit a blame accusation (a DKG participant, or any caller of the public `AdditionalBlameMachine` blame API, which is explicitly designed for non-participants to evaluate blame — pedpop/src/lib.rs:638-648) can crash every node that evaluates the accusation by naming a nonexistent participant index such as `n + 1`. Since `blame` deliberately prevents protocol completion on any fault, a crash here denies completion of key generation and aborts the validator set operation — a remote, single-message denial of service matching the CVE's availability impact (CVSS A:H).

### Likelihood Explanation
The trigger requires only sending a blame claim with an out-of-range `Participant` index — a single byte-level field the attacker fully controls. No collusion, leaked key, or special positioning is needed; `AdditionalBlameMachine` exists precisely so external parties can evaluate accusations. The only nuance: reaching the `commitments[&sender]` panic requires `proof: Some(...)` with a verifying DLEq, which is publicly constructible as shown, and reaching the `enc_keys[&decryptor]` panic requires only `proof: Some(...)` and an invalid `recipient`. Likelihood is high for any deployment that relays blame accusations for evaluation.

### Recommendation
Validate both `sender` and `recipient` in `blame_internal` (and in `AdditionalBlameMachine::blame`) against the registered participant set before any map access, returning a defined result (e.g., blaming the accuser or a dedicated `InvalidParticipant` error). Replace `self.commitments[&sender]` and `self.enc_keys[&decryptor]` with `.get()` lookups that handle absence gracefully. The `assert!` in `Decryption::register` (encryption.rs:356-359) should also become an error to avoid future duplicate-registration panics.

### Proof of Concept
```rust
// After a DKG among n participants, an accuser submits:
//   blame(sender = Participant::new(n + 1).unwrap(), recipient = Participant::new(1).unwrap(), msg, Some(proof))
// where msg.key = G*k, msg.pop is a Schnorr PoP over pop_challenge(.., sender=n+1, ..),
// and proof.key = enc_pub_key[1] * k with a valid DLEq — all built from public values.
// decrypt_with_proof indexes enc_keys[1] fine, the share parses, then
// blame_internal evaluates share_verification_statements over
// self.commitments[&Participant(n + 1)] (pedpop/src/lib.rs:599) -> HashMap index panic.
//
// Simpler variant: blame(sender = 1, recipient = Participant::new(n + 1).unwrap(), msg, Some(any_proof))
// -> self.enc_keys[&decryptor] at encryption.rs:388 -> panic.
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L582-604)
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
```

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

**File:** crypto/dkg/src/lib.rs (L27-36)
```rust
impl Participant {
  /// Create a new Participant identifier from a u16.
  pub const fn new(i: u16) -> Option<Participant> {
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }

```
