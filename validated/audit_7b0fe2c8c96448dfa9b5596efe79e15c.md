### Title
Out-of-range participant index in blame evaluation panics the verifier — (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`AdditionalBlameMachine::blame` / `BlameMachine::blame` take `sender` and `recipient` as arbitrary `Participant` values from the caller, then index `HashMap`s that only contain entries for participants `1..=n`. An accusation naming a participant index outside that range causes a `HashMap` index panic, crashing the evaluating party — a remotely triggerable denial of service analogous to CVE-2025-53042's crash/hang class.

### Finding Description
`AdditionalBlameMachine::new` populates `commitments` and `Decryption.enc_keys` strictly for `1 ..= n`: [1](#0-0) 

`blame` then forwards the caller-supplied `sender`/`recipient` unchecked into `blame_internal`, which dereferences those maps:

- `self.enc_keys[&decryptor]` inside `decrypt_with_proof` panics when `recipient` (the `decryptor`) was never registered: [2](#0-1) 

- `self.commitments[&sender]` panics when `sender` is out of range or wasn't among the registered participants: [3](#0-2) 

Neither `BlameMachine::blame` nor `AdditionalBlameMachine::blame` validates `sender`/`recipient` against `params`/`n` before these lookups. `AdditionalBlameMachine` is explicitly designed for callers "regardless of if the caller was a member in the DKG protocol" (lib.rs:639-648), so the inputs are attacker-controlled accusation fields, not internally derived indexes. The panic paths are also reachable through `BlameMachine::blame` for legitimate participants evaluating accusations forwarded from untrusted peers.

### Impact Explanation
Any party evaluating a blame proof — including a non-participant auditor using `AdditionalBlameMachine` — crashes on a single crafted accusation (e.g., `sender = Participant::new(n+1)` or `recipient = 0xFFFF`). In a validator/deployment context where blame evaluation is part of DKG abort handling, this converts an accusation message into a repeatable crash of the evaluating process, matching the CVE's availability-only impact (S:U/C:N/I:N/A:H).

### Likelihood Explanation
Triggering requires only submitting a blame tuple with an out-of-range `Participant` — public inputs with no secret knowledge or valid proof needed, since the panic occurs on the map lookup before any proof verification concludes. No race, malformed-curve encoding, or collusion is required; a lone accuser suffices.

### Recommendation
Validate `sender` and `recipient` in `blame_internal` (or in both `blame` wrappers) against the registered participant set — return a defined error/result instead of indexing. Replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `.get()` lookups that map a missing entry to a `PedPoPError`/`DecryptionError`, or reject out-of-range participants up front via `ThresholdParams` bounds checks.

### Proof of Concept
```rust
// Setup: run a PedPoP DKG (or construct an AdditionalBlameMachine) for n participants.
let mut commitment_msgs: HashMap<Participant, EncryptionKeyMessage<Ristretto, Commitments<Ristretto>>> = ...;
let machine = AdditionalBlameMachine::<Ristretto>::new(context, n, commitment_msgs).unwrap();

// Craft an accusation with a recipient index that was never registered (n + 1 <= u16::MAX).
let malicious_recipient = Participant::new(n + 1).unwrap();
let msg: EncryptedMessage<Ristretto, SecretShare<_>> = EncryptedMessage::read(&mut bytes, params).unwrap();
let proof: Option<EncryptionKeyProof<Ristretto>> = Some(EncryptionKeyProof::read(&mut proof_bytes).unwrap());

// Panics at crypto/dkg/pedpop/src/encryption.rs:388 on `self.enc_keys[&decryptor]`
// (or at crypto/dkg/pedpop/src/lib.rs:599 on `self.commitments[&sender]` if sender is out of range).
machine.blame(sender, malicious_recipient, msg, proof);
```

Note: I could not fully trace every upstream caller of `blame`/`AdditionalBlameMachine::new` in coordinator/processor code within the available iterations, so the exact deployment wiring is unverified — but the panic itself and its attacker-controlled inputs are confirmed in the in-scope crate, and `AdditionalBlameMachine` is explicitly exposed to non-participants, keeping this within the reachability rules.

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L596-602)
```rust
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
```

**File:** crypto/dkg/pedpop/src/lib.rs (L656-660)
```rust
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-389)
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
```
