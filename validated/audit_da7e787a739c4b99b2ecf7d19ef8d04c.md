### Title
Missing participant-index bounds check in `blame_internal` causes panic (DoS) on attacker-supplied blame claims - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The ClamAV bug class is improper input validation in a parser reachable by unauthenticated input, yielding a NULL dereference and DoS. The Serai analog is an unchecked `HashMap` index (`self.commitments[&sender]`) in PedPoP's blame-evaluation path. `BlameMachine::blame` and `AdditionalBlameMachine::blame` accept `sender`/`recipient` `Participant` values and an `EncryptedMessage` that originate from an accuser's claim. `Participant::new` only rejects zero, so any index `> n` is representable, yet `blame_internal` indexes `self.commitments[&sender]`, which panics when `sender` was not a DKG participant.

### Finding Description
`AdditionalBlameMachine::new` builds `commitments` keyed strictly by `Participant(1..=n)` [1](#0-0) . `blame_internal` then evaluates an accusation `(sender, recipient, msg, proof)`; after a successful `decrypt_with_proof` and a canonical scalar share, it dereferences `&self.commitments[&sender]` [2](#0-1) . `HashMap`'s `Index` impl panics on a missing key. No validation ties `sender`/`recipient` to the participant set — `Participant::new` only enforces non-zero, and blame inputs are adversary-controlled accusation data [3](#0-2) . The panic also aborts blame evaluation entirely (`blame` returns the faulty party only on success), so a single crafted accusation crashes the evaluator instead of returning a blame verdict.

### Impact Explanation
Any party able to submit a blame/accusation (the documented use case for `AdditionalBlameMachine`, which exists so non-participants can adjudicate blame [4](#0-3) ) can supply `sender = Participant(n+1)` plus a well-formed `EncryptedMessage`/proof and crash every node evaluating the claim. This mirrors the advisory: unauthenticated, remotely supplied input reaches a code path lacking a bounds/existence check and triggers a crash — a denial of service against DKG fault resolution, which can stall key rotation/recovery.

### Likelihood Explanation
The attacker only needs a valid `EncryptedMessage<C, SecretShare>` and `EncryptionKeyProof` for the ECDH key they choose (both are self-contained objects they can generate for any `sender` index, since `decrypt_with_proof` binds the proof to the message, not to the commitment set). `blame_internal` reaches the indexing after `decrypt_with_proof` returns `Ok` and `from_repr` succeeds [5](#0-4) , both fully attacker-satisfiable.

### Recommendation
In `blame_internal`, replace `self.commitments[&sender]` with `self.commitments.get(&sender)` and treat a missing `sender` (and out-of-range `recipient`) as a blameable/malformed accusation rather than panicking — e.g., return the accuser (`recipient`) as faulty or a dedicated error. Additionally, validate `sender`/`recipient` against `1..=n` at the top of `blame`/`AdditionalBlameMachine::blame`.

### Proof of Concept
```rust
// PedPoP with n = 3 participants completes; commitments has keys {1,2,3}.
let machine = AdditionalBlameMachine::<C>::new(context, 3, commitment_msgs).unwrap();

// Attacker crafts a syntactically valid EncryptedMessage + EncryptionKeyProof
// (ECDHPoK over their own key) and accuses a non-existent sender.
let fake_sender = Participant::new(4).unwrap(); // valid Participant: only 0 is rejected
let recipient   = Participant::new(1).unwrap();

// decrypt_with_proof succeeds (self-contained ECDH proof), share parses as a
// canonical scalar, then `self.commitments[&fake_sender]` panics:
// "HashMap index on missing key" -> thread panic / node DoS.
machine.blame(fake_sender, recipient, crafted_msg, Some(crafted_proof));
```

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L582-605)
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
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L638-648)
```rust
impl<C: Ciphersuite> AdditionalBlameMachine<C> {
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

**File:** crypto/dkg/pedpop/src/lib.rs (L649-662)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L674-682)
```rust
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
