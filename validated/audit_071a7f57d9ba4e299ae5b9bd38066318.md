### Title
Unvalidated sender/recipient indices in PedPoP blame handling cause a panic, crashing the node on a forged accusation - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` and their shared helper `blame_internal` accept an attacker-supplied `sender` and `recipient` `Participant` index along with an attacker-supplied `EncryptedMessage`/`EncryptionKeyProof`, and then index `self.enc_keys[&decryptor]` (inside `Decryption::decrypt_with_proof`) and `self.commitments[&sender]` with those unchecked indices. A blame accusation naming a `sender` or `recipient` that was never registered in the DKG panics instead of returning an error, crashing the evaluator. This is the Serai analog of CVE-2018-16542's class: insufficient index/bounds checking on the error-handling (blame) path turns malformed input into a crash.

### Finding Description
`blame_internal` at crypto/dkg/pedpop/src/lib.rs:575-609 calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)`. Inside `decrypt_with_proof` (crypto/dkg/pedpop/src/encryption.rs:366-397), after the (attacker-satisfiable) PoP signature check, the code evaluates:

```rust
&[self.enc_keys[&decryptor], *proof.key],
```

`enc_keys` is a `HashMap` keyed by `Participant`; `decryptor` is the caller-supplied `recipient`. `HashMap` indexing panics on a missing key, so an accusation naming a `recipient` that is not a registered DKG participant panics. If `recipient` is valid, decryption proceeds and `blame_internal` then evaluates `self.commitments[&sender]` (lib.rs:599) inside `share_verification_statements` — again a bare `HashMap` index, panicking when `sender` was not a participant.

Crucially, the attacker controls all the bytes needed to reach the panic:

- `msg: EncryptedMessage<C, SecretShare<C::F>>` is read via `EncryptedMessage::read` (encryption.rs:171-177), which performs only deserialization (`read_G`, `SchnorrSignature::read`, raw share bytes) — no semantic checks.
- `proof: Option<EncryptionKeyProof<C>>` is read via `EncryptionKeyProof::read` (encryption.rs:267-269), likewise unchecked until used.
- The `sender`/`recipient` indices come directly from the accusation and are never checked against `params.all_participant_indexes()` or the maps' key sets anywhere in `blame`/`blame_internal`/`decrypt_with_proof`.

The only gate before the panic is `msg.pop.verify(...)` (encryption.rs:374-379), which the attacker trivially satisfies by constructing the `EncryptedMessage` themselves (they choose `key` and sign the PoP). `AdditionalBlameMachine::new` (lib.rs:649-662) exists precisely to let non-participants evaluate blame over `commitment_msgs`, and it populates `enc_keys`/`commitments` only for `1..=n`, so any `sender`/`recipient` index `> n` (or simply absent) reaches the panic. The constructor's own doc comment acknowledges "may cause ... panics" from unauthenticated inputs, but `blame` itself takes raw, unverified attacker indices.

This mirrors the CVE class exactly: Ghostscript crashed because stack-size checks were skipped during error handling; here, membership checks are skipped during blame (error) handling, so malformed blame input aborts the process.

### Impact Explanation
An unprivileged party that can submit a blame accusation (a DKG participant, or any party able to route an accusation to a node running `AdditionalBlameMachine`) can crash the evaluating node mid-DKG by naming a non-existent `sender` or `recipient`. In a validator/processor deployment this is an availability failure of the key-generation/blame process triggered by a single crafted message — the same denial-of-service outcome as CVE-2018-16542 (CVSS 5.5, availability-only impact). Severity: Medium.

### Likelihood Explanation
Any participant in (or observer of) a PedPoP DKG session can issue an accusation; nothing requires the accused `sender` to be a real participant or the `msg`/`proof` to correspond to a real exchange. The PoP check is satisfiable by construction, so the panic is deterministic once a bad index is supplied. The only mitigating factor is that the crash is confined to the node evaluating blame and does not leak key material.

### Recommendation
Validate `sender` and `recipient` against the registered key sets before use: return a `PedPoPError` (e.g. `MissingParticipant`/`InvalidParticipant`) when `!self.enc_keys.contains_key(&decryptor)` or `!self.commitments.contains_key(&sender)`, in `Decryption::decrypt_with_proof` (encryption.rs:381-389) and `blame_internal` (lib.rs:575+). Replace the `HashMap` index operators with `.get()`/`ok_or` error propagation so malformed accusations abort the blame evaluation rather than the process.

### Proof of Concept
Setup: run a PedPoP `KeyMachine::calculate_share` to obtain a `BlameMachine` for a session with `n` participants, or construct `AdditionalBlameMachine::new(context, n, commitment_msgs)` with all honest participants' commitment messages.

Trigger: call `blame` (or `AdditionalBlameMachine::blame`) with `recipient = Participant::new(n + 1).unwrap()` (an index never registered) and any attacker-constructed `EncryptedMessage` whose `pop` signature is valid (trivially producible by encrypting to a self-chosen `key` and signing `pop_challenge`). Execution reaches `decrypt_with_proof`, and `self.enc_keys[&decryptor]` at crypto/dkg/pedpop/src/encryption.rs:388 panics on the missing map entry — a crash reachable entirely from attacker-supplied indices and bytes.

Similarly, keeping `recipient` valid but setting `sender = Participant::new(n + 1)` and supplying a valid `proof` causes `self.commitments[&sender]` at crypto/dkg/pedpop/src/lib.rs:599 to panic after successful decryption.

Caveat: reachability depends on the integrator passing accusation-supplied `sender`/`recipient` indices into `blame` without pre-validation — the API offers no checked alternative, and `AdditionalBlameMachine` is explicitly designed for third-party blame evaluation, so this is the intended call pattern. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L171-177)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-392)
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

**File:** crypto/dkg/pedpop/src/lib.rs (L649-683)
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
}
```
