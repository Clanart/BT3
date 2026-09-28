### Title
Unauthenticated panic in PedPoP blame evaluation via out-of-range `Participant` index causes denial of service - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The `BlameMachine::blame` / `AdditionalBlameMachine::blame` APIs in `crypto/dkg/pedpop` accept attacker-controlled `sender`/`recipient` `Participant` values plus raw `EncryptedMessage`/`EncryptionKeyProof` bytes, and index internal `HashMap`s with those participants without bounds checks. A `Participant` is any nonzero `u16` (1..=65535), while `enc_keys` and `commitments` only contain entries for the `n` actual DKG members. Supplying `sender` or `recipient` greater than `n` causes an indexing panic (`HashMap` `[]` operator), crashing the caller — an analog of the CVE-2020-19716 class: untrusted input reaching an unchecked access → denial of service.

### Finding Description
`Decryption::decrypt_with_proof` verifies an optional `EncryptionKeyProof` DLEq against `self.enc_keys[&decryptor]`, which panics if `decryptor` was never registered: [1](#0-0) 

`enc_keys` is only populated via `Decryption::register`, called for participants 1..=n in `AdditionalBlameMachine::new` (and for validated round-1 commitment senders in `SecretShareMachine::verify_r1`): [2](#0-1) 

The panic is reachable because `blame_internal` passes the caller-supplied `recipient` straight into `decrypt_with_proof` with no check that it is a DKG member: [3](#0-2) 

A second, symmetric panic exists at `self.commitments[&sender]` when `sender` is out of range: [4](#0-3) 

Note the panic on `enc_keys[&decryptor]` fires *before* the message even needs to decrypt: it occurs while evaluating the DLEq proof whenever `proof` is `Some`, and `EncryptionKeyProof::read` happily parses any two group elements plus a DLEq proof from attacker bytes: [5](#0-4) 

The `BlameMachine` path via `calculate_share` also leaves `enc_keys` populated only for registered senders, so both public entry points (`BlameMachine::blame`, `AdditionalBlameMachine::blame`) are exposed. The crate itself documents that invalid inputs "may cause … panics" only for *commitment* messages (`AdditionalBlameMachine::new` docs), not for the `sender`/`recipient`/`msg`/`proof` arguments to `blame`, which are the untrusted-accusation inputs.

### Impact Explanation
Any node/validator evaluating a blame accusation — a routine part of PedPoP DKG finalization where one party accuses another of sending a bad share — can be crashed by an accusation naming a `Participant` index outside 1..=n (e.g., `Participant(200)` in a 150-validator set). Panics in this context abort the task; in Serai's deployment model (coordinator/processor binaries use panic hooks that `process::exit` on task panics), this is a full node DoS, directly matching the CVE's "buffer overflow → DOS" class mapped onto Rust's bounds-checked panic equivalent.

### Likelihood Explanation
Blame evaluation is triggered by untrusted protocol traffic: the accusing party chooses `sender`, `recipient`, the serialized `EncryptedMessage` (`EncryptedMessage::read`), and the `EncryptionKeyProof` (`EncryptionKeyProof::read`). All are reachable with public inputs per the engagement rules. The only precondition is that a node runs `blame`, which is the documented recovery path whenever a DKG share is disputed — i.e., precisely when a malicious participant is already active. No collusion or key access required.

### Recommendation
In `Decryption::decrypt_with_proof`, replace `self.enc_keys[&decryptor]` with a checked lookup (`self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?`). In `blame_internal`, validate `sender`/`recipient` membership (`self.commitments.get(&sender)`, and check `recipient` against `enc_keys`) before indexing, returning an error or blame verdict rather than panicking. Alternatively, bound `Participant` values against `params.n()` at the `blame`/`AdditionalBlameMachine::blame` entry points.

### Proof of Concept
```rust
// In-scope: crypto/dkg/pedpop
// Setup: construct an AdditionalBlameMachine for a DKG with n = 3 participants.
let machine = AdditionalBlameMachine::<Ristretto>::new(
  context,            // [u8; 32] DKG context
  3,                  // n = 3 participants -> enc_keys has keys {1,2,3}
  commitment_msgs,    // EncryptionKeyMessage<Commitments> for participants 1..=3
).unwrap();

// Attacker-submitted accusation: recipient index 4 was never registered.
let bytes = /* any bytes that parse: G || SchnorrSignature || SecretShare repr */;
let msg = EncryptedMessage::<Ristretto, SecretShare<_>>::read(
  &mut bytes.as_slice(),
  ThresholdParams::new(2, 3, 1).unwrap(),
).unwrap();
let proof_bytes = /* G || DLEqProof (c, s scalars) */;
let proof = EncryptionKeyProof::<Ristretto>::read(&mut proof_bytes.as_slice()).unwrap();

// Panics at encryption.rs:388 on self.enc_keys[&Participant(4)] ->
// "no entry found for key" -> task panic / process abort (DoS)
machine.blame(Participant::new(1).unwrap(), Participant::new(4).unwrap(), msg, Some(proof));
```

The same panic triggers via `self.commitments[&sender]` in `blame_internal` when `sender` is `Participant::new(5)` while only 1..=3 registered commitments.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L266-269)
```rust
impl<C: Ciphersuite> EncryptionKeyProof<C> {
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Ok(Self { key: Zeroizing::new(C::read_G(reader)?), dleq: DLEqProof::read(reader)? })
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

**File:** crypto/dkg/pedpop/src/lib.rs (L575-588)
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
```

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

**File:** crypto/dkg/pedpop/src/lib.rs (L649-661)
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
```
