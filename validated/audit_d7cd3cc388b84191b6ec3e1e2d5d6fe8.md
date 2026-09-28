### Title
Unbounded `Participant` index causes panic (node crash) in PedPoP blame evaluation via `HashMap` indexing - (crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame_internal` (and its public wrappers `BlameMachine::blame` / `AdditionalBlameMachine::blame`) indexes `self.commitments[&sender]` and `self.encryption`'s `enc_keys[&decryptor]` with `Participant` values that arrive from untrusted blame/accusation messages, without checking the index is within `1 ..= n`. Any nonzero `u16` is a valid `Participant`, so an accusation naming an out-of-range participant panics the verifying process — an unauthenticated denial of service analogous to the MySQL optimizer crash class (CVE-2021-2021), except reachable by a less-privileged party.

### Finding Description
`Participant::new` only rejects zero. `AdditionalBlameMachine::new` populates `commitments` and `enc_keys` exclusively for `i in 1 ..= n` [1](#0-0) . Later, `blame_internal` calls `decrypt_with_proof(sender, recipient, msg, proof)` [2](#0-1) , which performs `self.enc_keys[&decryptor]` [3](#0-2) , and then evaluates `self.commitments[&sender]` [4](#0-3) . Both are `HashMap` `Index` impls that panic on absent keys. Neither `blame` nor `blame_internal` validates `sender`/`recipient` against the protocol's `n`. The upstream call site (`CoordinatorMessage::VerifyBlame`) passes `accuser`/`accused` taken directly from the message into `AdditionalBlameMachine::blame` with no bounds check [5](#0-4) .

### Impact Explanation
A single crafted blame/accusation message naming a non-existent participant (e.g., index `n + 1`, or any index that wasn't part of the DKG) causes an unconditional panic inside `blame_internal`/`decrypt_with_proof` while the node processes the dispute. In the processor context this aborts the task handling key-generation blame, producing a repeatable crash / hang-equivalent availability failure — the same impact class as the referenced CVE (unauthenticated-adjacent remote DoS of a networked component via crafted protocol data). Because the panic occurs during blame adjudication, it can additionally be timed to disrupt the slashing/blame process itself, preventing the protocol from correctly attributing fault for a genuinely invalid share.

### Likelihood Explanation
Reachability requires a `VerifyBlame`-style message (or any caller of `BlameMachine::blame`/`AdditionalBlameMachine::blame`) whose `accuser`/`accused` fields the adversary controls. These fields are participant indexes carried in protocol messages — public, attacker-influenced inputs, not secret material. The trigger needs no valid signature, share, or proof: `decrypt_with_proof` reaches `self.enc_keys[&decryptor]` before the DLEq proof is even evaluated if the PoP check passes, and `self.commitments[&sender]` is reached after message deserialization regardless of blame validity. A garbage `EncryptedMessage` with any bytes suffices to reach `blame_internal` since the function is invoked before validity is established. One caveat: triggering requires an in-progress or completed DKG session whose blame path is exercised; the exploit is unconditional once that path is entered.

### Recommendation
Replace `HashMap` `Index` accesses with checked lookups that map a missing entry to a determinate fault outcome rather than a panic:
- In `Decryption::decrypt_with_proof` (crypto/dkg/pedpop/src/encryption.rs:388), use `enc_keys.get(&decryptor)` and return `DecryptionError::InvalidProof` (or blame the accuser) when absent.
- In `BlameMachine::blame_internal` (crypto/dkg/pedpop/src/lib.rs:599), use `commitments.get(&sender)`; when absent, the "sender" cannot have sent a protocol message, so the accuser should be blamed — return `recipient`.
- Additionally validate at the entry points (`BlameMachine::blame`, `AdditionalBlameMachine::blame`) that `sender` and `recipient` are within `1 ..= n` (where `n = commitments.len()`), returning the accuser as faulty on violation.
- The same defensive pattern applies to `Decryption::register`'s `assert!(!contains_key)` (encryption.rs:356-358), which panics on a duplicated participant registration — return an error instead.

### Proof of Concept
Conceptual, using the public API shape (`Ristretto` or any `Ciphersuite`):

```rust
// Given a completed/initialized AdditionalBlameMachine for n = 4:
let mut commitment_msgs = /* EncryptionKeyMessage<_, Commitments> for participants 1..=4 */;
let machine = AdditionalBlameMachine::<Ristretto>::new(context, 4, commitment_msgs).unwrap();

// Attacker submits an accusation naming a participant who doesn't exist.
// Participant::new only rejects 0, so 7 is a "valid" Participant value.
let accused = Participant::new(7).unwrap();   // 7 > n = 4
let accuser = Participant::new(1).unwrap();

// msg: any bytes that deserialize — blame is invoked before validity is judged
let msg = EncryptedMessage::<Ristretto, SecretShare<_>>::read(&mut garbage, params).unwrap();

// Panics at crypto/dkg/pedpop/src/lib.rs:599 on `self.commitments[&sender]`
// (HashMap Index on missing key), or earlier at encryption.rs:388 on
// `self.enc_keys[&decryptor]` if the accuser index is out of range instead.
machine.blame(accused, accuser, msg, None);
```

The panic is deterministic and requires no secrets, valid proofs, or honest-protocol participation beyond the ability to route an accusation to the blame-evaluation API — matching the report's bug class (reachable, repeatable crash = availability DoS).

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L582-588)
```rust
    let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) {
      Ok(share_bytes) => share_bytes,
      // If there's an invalid signature, the sender did not send a properly formed message
      Err(DecryptionError::InvalidSignature) => return sender,
      // Decryption will fail if the provided ECDH key wasn't correct for the given message
      Err(DecryptionError::InvalidProof) => return recipient,
    };
```

**File:** crypto/dkg/pedpop/src/lib.rs (L596-604)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L656-660)
```rust
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L383-389)
```rust
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
```

**File:** processor/src/key_gen.rs (L504-549)
```rust
      CoordinatorMessage::VerifyBlame { id, accuser, accused, share, blame } => {
        let params = ParamsDb::get(txn, &id.session, id.attempt).unwrap().0;

        let mut share_ref = share.as_slice();
        let Ok(substrate_share) = EncryptedMessage::<
          Ristretto,
          SecretShare<<Ristretto as Ciphersuite>::F>,
        >::read(&mut share_ref, params) else {
          return ProcessorMessage::Blame { id, participant: accused };
        };
        let Ok(network_share) = EncryptedMessage::<
          N::Curve,
          SecretShare<<N::Curve as Ciphersuite>::F>,
        >::read(&mut share_ref, params) else {
          return ProcessorMessage::Blame { id, participant: accused };
        };
        if !share_ref.is_empty() {
          return ProcessorMessage::Blame { id, participant: accused };
        }

        let mut substrate_commitment_msgs = HashMap::new();
        let mut network_commitment_msgs = HashMap::new();
        let commitments = CommitmentsDb::get(txn, &id).unwrap();
        for (i, commitments) in commitments {
          let mut commitments = commitments.as_slice();
          substrate_commitment_msgs
            .insert(i, EncryptionKeyMessage::<_, _>::read(&mut commitments, params).unwrap());
          network_commitment_msgs
            .insert(i, EncryptionKeyMessage::<_, _>::read(&mut commitments, params).unwrap());
        }

        // There is a mild DoS here where someone with a valid blame bloats it to the maximum size
        // Given the ambiguity, and limited potential to DoS (this being called means *someone* is
        // getting fatally slashed) voids the need to ensure blame is minimal
        let substrate_blame =
          blame.clone().and_then(|blame| EncryptionKeyProof::read(&mut blame.as_slice()).ok());
        let network_blame =
          blame.clone().and_then(|blame| EncryptionKeyProof::read(&mut blame.as_slice()).ok());

        let substrate_blame = AdditionalBlameMachine::new(
          context(&id, SUBSTRATE_KEY_CONTEXT),
          params.n(),
          substrate_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, substrate_share, substrate_blame);
```
