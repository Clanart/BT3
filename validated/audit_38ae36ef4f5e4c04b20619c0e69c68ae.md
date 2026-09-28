### Title
Unauthenticated blame verdicts let any party declare an arbitrary honest participant faulty without a decryption proof - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The "lost password" bug class is an unauthenticated request causing a security-relevant state change against an arbitrary account. Serai's analog is `BlameMachine::blame` / `Decryption::decrypt_with_proof`: when an accusation is submitted with `proof: None`, the code unconditionally returns the accused `recipient` as the faulty party. No check ties the accuser to the claimed recipient, and no proof of decryption failure is required — any caller can fabricate a blame statement that convicts any participant.

### Finding Description
`BlameMachine::blame` delegates to `blame_internal(sender, recipient, msg, proof)`, which calls `Decryption::decrypt_with_proof`. The intended logic is:

- Invalid per-message PoP signature → `sender` is faulty.
- Invalid/missing encryption-key proof → `recipient` (the accuser) is faulty.
- Valid decryption revealing a bad share → `sender` is faulty.

The `None` branch exists for accusations of an *invalid signature*: "There's no encryption key proof if the accusation is of an invalid signature" (encryption.rs). But `decrypt_with_proof` checks the signature first and only then evaluates `proof`:

```rust
// crypto/dkg/pedpop/src/encryption.rs
if let Some(proof) = proof {
  proof.dleq.verify(...)?;
  ...
} else {
  Err(DecryptionError::InvalidProof)
}
```

```rust
// crypto/dkg/pedpop/src/lib.rs
Err(DecryptionError::InvalidSignature) => return sender,
Err(DecryptionError::InvalidProof) => return recipient,
```

A message carrying a *valid* PoP signature plus `proof = None` therefore always returns `recipient` as faulty. The `recipient` field is an unconstrained `Participant` argument chosen by the caller — `blame` never verifies that the accusation originated from that participant, that the recipient is the decryptor, or that the accuser possesses the ECDH secret. An attacker supplies a self-crafted `EncryptedMessage` (they can always produce one with a valid PoP since `pop_challenge` binds the declared `sender`, which they set to themselves) and names any honest participant as `recipient`. Every arbitrator running `blame` deterministically convicts the victim.

### Impact Explanation
`BlameMachine` is the public adjudication API: "Given an accusation of fault, determine the faulty party". In Serai's processor flow, blame results feed `ProcessorMessage::InvalidShare`/fault reporting, which is the input to slashing/malicious-participant logic and forces protocol abort. A forged verdict against an honest validator can:

- Falsely mark them faulty, contributing to slashing or exclusion from the validator set.
- Abort the DKG for the honest majority ("prevent completion of the machine, forcing an abort of the protocol" is the documented consequence of identified blame).

Like CVE-2016-9479, this is a missing authorization check on an action that affects a target of the attacker's choosing: conviction is assigned by argument, not by cryptographic proof.

### Likelihood Explanation
Requires only the ability to submit an accusation plus attacker-controlled `sender`/`recipient`/`msg`/`proof` parameters — all public inputs (`EncryptedMessage::read` accepts untrusted bytes). No secret knowledge is needed for the `proof = None` path. Exploitation does require a deployment where third parties or validators adjudicate blame via this API and act on `recipient`-faulty verdicts, so it is a conditional-but-reachable High/Medium rather than a pure-library edge case.

### Recommendation
- Do not allow `proof = None` to convict the `recipient`. `None` should only be meaningful for the invalid-signature branch; if the signature is valid and no decryption proof is supplied, the accusation should be rejected as unverifiable (or default to blaming the accuser only when `recipient` is authenticated as the accuser).
- Bind the accusation to the accuser cryptographically: require the encryption-key proof (DLEq against `self.enc_keys[&decryptor]`) for every verdict that can convict a recipient, and treat missing/invalid proofs as `sender`-faulty or as an invalid accusation rather than `recipient`-faulty.
- Alternatively, take the accuser's `Participant` identity from the authenticated caller rather than as a free parameter in `blame`.

### Proof of Concept
```rust
// Attacker is participant 1; victim (to be falsely convicted) is participant 3.
// 1. Attacker crafts any EncryptedMessage with a valid PoP over sender=1
//    (they hold the per-message key, so this always verifies).
let forged_msg: EncryptedMessage<C, SecretShare<C::F>> = attacker_msg; // valid pop

// 2. Attacker submits an "accusation": sender = some participant, recipient = victim.
let (additional, faulty) = blame_machine.blame(
    SENDER,        // e.g. attacker or any participant
    VICTIM,        // arbitrary honest participant
    forged_msg,    // valid PoP -> skips InvalidSignature branch
    None,          // no proof -> decrypt_with_proof => InvalidProof
);

// 3. blame_internal maps Err(InvalidProof) -> `return recipient`,
//    so `faulty == VICTIM` with zero evidence.
assert_eq!(faulty, VICTIM);
```

Relevant code: `Decryption::decrypt_with_proof` rejects `None` proofs as `InvalidProof` [1](#0-0) , and `blame_internal` maps `InvalidProof` to `recipient` [2](#0-1) .

### Citations

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
