### Title
Unvalidated `sender`/`recipient` indexes in PedPoP blame evaluation cause a reachable panic (assertion-failure DoS) - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The analog of CVE-2024-4076 (an assertion failure reachable via crafted protocol input, yielding denial of service) is present in PedPoP's blame resolution. `BlameMachine::blame` / `AdditionalBlameMachine::blame` accept arbitrary `sender` and `recipient` `Participant` indexes supplied as part of a remote accusation, then index `HashMap`s with them. An out-of-range or unregistered index triggers a panic, crashing the process evaluating blame and aborting the DKG.

### Finding Description
`BlameMachine::blame` forwards attacker-controlled `sender`/`recipient` indexes to `blame_internal`, which performs two unchecked map lookups:

1. `self.commitments[&sender]` in `crypto/dkg/pedpop/src/lib.rs:599` — `commitments` only contains participants `1..=n`. Passing `sender > n` panics.
2. `self.encryption.decrypt_with_proof(...)` → `self.enc_keys[&decryptor]` in `crypto/dkg/pedpop/src/encryption.rs:388` — `enc_keys` is populated by `Decryption::register`, which in the `KeyMachine` flow is called only for the *other* participants (`verify_r1` registers each `l` from `commitment_msgs`, and our own index `i` is never registered because `validate_map` rejects a map containing `ours`). Passing `recipient == params.i()` (us) or any `recipient > n` panics.

Neither `blame` nor `blame_internal` bounds-check `sender`/`recipient` against `1..=n` or against membership in the maps. The same applies to `AdditionalBlameMachine::blame`, whose doc explicitly notes it can evaluate blame "regardless of if the caller was a member in the DKG protocol" — i.e., it is designed to process accusations about arbitrary party pairs, yet still panics on non-member indexes.

### Impact Explanation
A panic in `blame` aborts the calling process. In Serai's deployment, blame evaluation is invoked when a participant accuses another of sending a faulty share — an accusation message is untrusted input from a counterparty. A malicious DKG participant can force every honest node evaluating their accusation to crash, halting key generation and any dependent signing. This maps directly onto the BIND bug class: a remote input reaching an unchecked assertion/implicit invariant (`HashMap` indexing) and causing a crash. No keys are leaked, but availability of the threshold protocol is destroyed — analogous severity to the upstream CVSS 7.5 availability-only issue.

### Likelihood Explanation
The trigger is a single blame evaluation with an out-of-domain index (e.g., `recipient` equal to the evaluator's own index, or `sender = n + 1`). It requires no collusion, no valid proofs, and no cryptographic work — just convincing the node to evaluate a blame statement with an arbitrary `Participant` index, which the API explicitly accepts. `Participant::new` only rejects zero, so any value `> n` is representable.

### Recommendation
Validate `sender` and `recipient` in `blame`/`blame_internal` before indexing: return the accusing party (or an error) when `u16::from(sender) > params.n`, `u16::from(recipient) > params.n`, `sender == recipient`, or the relevant maps lack the key. `Decryption::register`'s `assert!` and the `enc_keys[&decryptor]` lookup should likewise be converted to fallible lookups rather than panics.

### Proof of Concept
```rust
// Honest node with ThresholdParams::new(t, n, i) completes generate_secret_shares and
// calculate_share, obtaining BlameMachine `bm`. A malicious participant submits an
// accusation naming a non-existent sender (or accusing with recipient == our own index):
let msg: EncryptedMessage<_, SecretShare<_>> = EncryptedMessage::read(&mut attacker_bytes, params).unwrap();
// Participant::new(n + 1) succeeds since it only rejects 0
let sender = Participant::new(n + 1).unwrap();
// Inside blame_internal:
//   decrypt_with_proof -> self.enc_keys[&decryptor]  (ok if recipient valid)
//   share_verification_statements(recipient, &self.commitments[&sender], ...)
//   -> HashMap index panic: process aborts.
bm.blame(sender, recipient, msg, proof);
// Alternatively, blame(_, recipient = params.i(), ...) panics in decrypt_with_proof
// since enc_keys never contains our own index.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-362)
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
