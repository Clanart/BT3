### Title
Panic on unregistered `Participant` index in DKG blame/decrypt path reachable from untrusted accusation input - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
Analogous to CVE-2023-49083 (NULL-deref when deserializing/handling an untrusted PKCS7 blob), `BlameMachine::blame` / `AdditionalBlameMachine::blame` dereference `HashMap` entries keyed on caller-supplied `Participant` values without validating membership. `blame_internal` passes `recipient` into `Decryption::decrypt_with_proof`, which indexes `self.enc_keys[&decryptor]`, and also indexes `self.commitments[&sender]` when checking the decrypted share. Both index operations panic if the named participant was never registered, crashing the process — a reachable Denial of Service equivalent to the reported segfault class.

### Finding Description
- `Decryption::decrypt_with_proof` performs `&self.enc_keys[&decryptor]` unconditionally when a proof is supplied. `enc_keys` only contains indexes registered in round 1 (`Encryption::register` → `Decryption::register`). Any `recipient` not in `{1..=n}` panics. [1](#0-0) 
- `blame_internal` performs `&self.commitments[&sender]`, panicking for any `sender` that is not a DKG participant whose commitments were loaded. [2](#0-1) 
- The entry points `BlameMachine::blame(sender, recipient, msg, proof)` and `AdditionalBlameMachine::blame(...)` take both `Participant` values straight from the accusation being processed, and neither validates them against the participant set before the indexing occurs. `Participant::new` accepts any nonzero `u16`, so indexes like `0xFFFF` are representable. [3](#0-2) 
- `AdditionalBlameMachine` is explicitly designed for non-participant arbiters ("capable of evaluating Blame regardless of if the caller was a member"), meaning these fields are consumed in a context where accusation metadata is attacker-influenced. The documented panic warning covers only invalid *commitment messages*, not the `sender`/`recipient` arguments to `blame`. [4](#0-3) 
- The msg/proof bytes themselves are read via `EncryptedMessage::read`/`EncryptionKeyProof::read`, which are safe, but the panic is triggered by the index fields accompanying them, not by malformed bytes. [5](#0-4) 

### Impact Explanation
Like the upstream NULL-deref, this converts untrusted protocol input into an unconditional abort. Any node/service that evaluates blame accusations — including the third-party `AdditionalBlameMachine` path explicitly built for that purpose — will panic and terminate when an accusation names a `recipient` (with `proof: Some`) or a `sender` outside the registered participant set. This aborts the DKG/blame-adjudication process and, in an embedded deployment (e.g., a service whose panic hook exits on task panic), kills the host process: an availability loss reachable with no privileged position beyond submitting an accusation referencing a non-existent participant index.

### Likelihood Explanation
Reachability requires only that accusations route attacker-influenced `(sender, recipient)` fields into `blame` — the exact inputs the API is designed to accept — and that the accuser is a protocol participant (the standard threat model for DKG fault claims). No malformed serialization, no collusion, and no broken assumption about the channel is needed; a single accusation naming `recipient = Participant(0xFFFF)` with `proof = Some(...)` hits `enc_keys[&decryptor]` and panics before any cryptographic check. Severity is Medium: it is a clean crash (DoS) with no secret leakage or correctness violation, matching the CVSS A:H-only profile of the source advisory.

Uncertainty: I could not fully confirm within the in-scope index whether upstream callers (coordinator/processor layers are out of scope) wrap `blame` calls with participant-set validation; the panic exists in the in-scope crate itself and the API contract does not require callers to pre-validate these indexes.

### Recommendation
Replace the panicking `HashMap` index operations with checked lookups and return the accusing/accused party as faulty (or a dedicated error) instead of aborting:

- In `Decryption::decrypt_with_proof`, use `self.enc_keys.get(&decryptor)` and return `DecryptionError::InvalidProof` (or blame the accuser) when absent. [1](#0-0) 
- In `blame_internal`, fetch `self.commitments.get(&sender)` and treat a missing sender as the accuser's fault (`return recipient`). [2](#0-1) 
- Optionally validate `sender`/`recipient` against `params.all_participant_indexes()` at the top of `blame` for defense in depth.

### Proof of Concept
```rust
// After a 2-of-3 PedPoP DKG, any party (or the third-party arbiter path) evaluates
// an accusation naming a recipient that was never registered:
use pedpop::AdditionalBlameMachine;
use dkg::Participant;

// `machine` was built via AdditionalBlameMachine::new(context, 3, commitment_msgs)
// or BlameMachine from a completed KeyMachine. The accuser claims recipient = 0xFFFF
// and supplies a syntactically valid EncryptedMessage + Some(proof):
let sender = Participant::new(1).unwrap();
let bogus_recipient = Participant::new(0xFFFF).unwrap();

// Panics inside Decryption::decrypt_with_proof at `self.enc_keys[&decryptor]`
// because enc_keys only holds participants 1..=3:
machine.blame(sender, bogus_recipient, msg, Some(proof));

// Symmetrically, `blame(Participant(0xFFFF), valid_recipient, msg, None)` reaches
// `self.commitments[&sender]` in blame_internal only if the earlier pop check
// passes; the decryptor-index panic above is the unconditional trigger.
```

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-177)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
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

**File:** crypto/dkg/pedpop/src/lib.rs (L595-604)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L639-662)
```rust
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
