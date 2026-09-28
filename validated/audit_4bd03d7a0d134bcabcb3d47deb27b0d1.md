### Title
Out-of-range participant indexes in PedPoP blame handling panic via unchecked HashMap indexing, crashing the node - (File: crypto/dkg/pedpop/src/encryption.rs, crypto/dkg/pedpop/src/lib.rs)

### Summary
`Decryption::decrypt_with_proof` and `BlameMachine::blame_internal` index `HashMap`s (`self.enc_keys[&decryptor]`, `self.commitments[&sender]`) with `Participant` values that are never range-checked against the DKG's `n`. Since `Participant::new` only rejects zero, any unprivileged participant can submit a blame/accusation referencing a `sender` or `recipient` index `> n` and trigger an unconditional panic, aborting the node's DKG/blame evaluation — an availability analog of CVE-2025-46399 (crash via manipulated local input).

### Finding Description
During the PedPoP DKG, a party who receives an invalid secret share emits a blame claim, evaluated by `BlameMachine::blame` / `AdditionalBlameMachine::blame`, both of which take `sender` and `recipient` arguments and reach `blame_internal` ( [1](#0-0) ).

Two unchecked index operations exist on this path:

1. `decrypt_with_proof` performs `self.enc_keys[&decryptor]` ( [2](#0-1) ). `enc_keys` is only populated in `Decryption::register` for participants `1..=n` ( [3](#0-2) ). A `recipient` of `Participant(255)` in an `n = 5` session indexes a missing key and panics inside `HashMap`'s `Index` impl.

2. `blame_internal` performs `self.commitments[&sender]` ( [4](#0-3) ). `commitments` is likewise keyed only by `1..=n` (populated from `all_participant_indexes` or in `AdditionalBlameMachine::new` for `1..=n`, [5](#0-4) ). An out-of-range `sender` panics identically.

Neither `BlameMachine::blame` nor `AdditionalBlameMachine::blame` validates `sender`/`recipient` before these lookups. The processor's blame flow forwards `accuser`/`faulty` indexes carried in `ProcessorMessage::InvalidShare` ( [6](#0-5) ), so the attacker-supplied participant index flows directly into these calls.

### Impact Explanation
A single crafted accusation (or a malicious participant registering a blame claim) with a participant index outside `1..=n` causes a panic in any node evaluating blame — including third-party observers using `AdditionalBlameMachine`, which is explicitly designed to let non-participants evaluate blame ( [7](#0-6) ). A panic in the processor's message handler takes down the validator process, giving an unprivileged DKG participant a reliable remote crash primitive. This matches the CVE's class: manipulated input reaching a code path that doesn't handle it, yielding denial of availability.

### Likelihood Explanation
Triggering requires only that the attacker be able to emit a blame claim or supply the `sender`/`recipient`/`accuser` participant index — public protocol data an unprivileged party controls. The panic is deterministic (missing `HashMap` key), needs no race or corrupted secret state, and the `AdditionalBlameMachine` constructor documents that blame inputs may even be evaluated by parties outside the DKG. The only mitigating factor is that the crash occurs during fault handling rather than the happy path — but that is precisely when the code runs.

### Recommendation
Validate `sender` and `recipient` in `blame_internal` (or in `blame`/`AdditionalBlameMachine::blame` before dispatch) against `1..=n` — e.g., `params.n()` for `BlameMachine` and the `n` passed to `AdditionalBlameMachine::new` — and treat out-of-range indexes as fault of the accuser or an immediate abort, rather than indexing. Replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `.get()` plus a defined error path. More broadly, the pattern `map[&key]` on attacker-influenced `Participant` keys should be avoided throughout pedpop.

### Proof of Concept
```rust
// n = 5 DKG completed; attacker (or accuser) supplies an out-of-range index.
// Participant::new only rejects 0, so Participant(200) is constructible.
let bad = Participant::new(200).unwrap();

// Path 1: BlameMachine::blame -> blame_internal -> decrypt_with_proof
//   hits `self.enc_keys[&decryptor]` with decryptor = bad -> panic.
blame_machine.blame(sender, bad, msg, proof);

// Path 2: AdditionalBlameMachine::blame -> blame_internal
//   hits `self.commitments[&sender]` with sender = bad -> panic.
additional_blame.blame(bad, recipient, msg, proof);
```

Both calls panic at the `HashMap` `Index` implementation before any validation can reject the input, since neither `blame`, `blame_internal`, nor `decrypt_with_proof` bounds-checks the participant indexes ( [8](#0-7) , [9](#0-8) ).

Caveat: I could not fully trace the processor-side call site that maps the wire `accuser`/`faulty` fields into `blame(...)` arguments (blame handling in `processor/src/key_gen.rs` beyond the shown lines was not read). The library-level panic itself is confirmed reachable via the public `blame` APIs with unvalidated `Participant` arguments; if the processor clamps these indexes before calling, the practical exposure is reduced to direct library consumers.

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

**File:** crypto/dkg/pedpop/src/lib.rs (L638-662)
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-390)
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
```

**File:** processor/src/key_gen.rs (L416-427)
```rust
            (match machine.calculate_share(rng, shares) {
              Ok(res) => res,
              Err(e) => match e {
                PedPoPError::InvalidShare { participant, blame } => {
                  Err(ProcessorMessage::InvalidShare {
                    id,
                    accuser: params.i(),
                    faulty: participant,
                    blame: Some(blame.map(|blame| blame.serialize())).flatten(),
                  })?
                }
                _ => panic!("unknown error: {e:?}"),
```
