### Title

Out-of-range blame recipient causes a panic and aborts DKG dispute handling - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary

`AdditionalBlameMachine::blame` accepts attacker-controlled `Participant` identifiers and forwards them to `Decryption::decrypt_with_proof`. That function indexes `self.enc_keys[&decryptor]` without checking whether the recipient participated in the DKG. A nonzero participant ID outside `1..=n` therefore panics after the supplied encrypted share passes its proof-of-possession check.

### Finding Description

`Participant::new` only rejects zero; any other `u16` is accepted, including values greater than the configured participant count `n` [1](#0-0) . `AdditionalBlameMachine::new` only registers encryption keys and commitments for participants `1..=n` [2](#0-1) .

However, `AdditionalBlameMachine::blame` exposes caller-controlled `sender` and `recipient` values and calls `blame_internal` without validating them against the machine's participant set [3](#0-2) . `blame_internal` forwards the recipient as `decryptor` [4](#0-3) . During proof verification, `decrypt_with_proof` directly indexes `self.enc_keys[&decryptor]` [5](#0-4) . For a syntactically valid nonzero participant not present in that map, indexing a `HashMap` with `[]` panics.

The encrypted message bytes are attacker-controlled through `EncryptedMessage::read`, which parses the sender's encryption key, Schnorr proof-of-possession, and ciphertext [6](#0-5) . The PoP verification occurs before the out-of-bounds map access [7](#0-6) .

### Impact Explanation

A malformed blame request can crash the process or task handling DKG disputes. Repeated submissions provide a repeatable denial of service against key-generation/dispute resolution. This is comparable to the reported bug class: attacker-supplied protocol input reaches an internal consistency assumption and causes a complete crash rather than returning an error.

### Likelihood Explanation

The attacker only needs to supply a `Participant` such as `n + 1` and an `EncryptedMessage` whose included Schnorr PoP verifies. `Participant` admits all nonzero `u16` values, while `AdditionalBlameMachine` stores keys only for `1..=n` [1](#0-0) [8](#0-7) . The panic does not require an invalid curve point, malformed scalar, leaked key, or control over internal state.

### Recommendation

Validate `sender` and `recipient` before calling `blame_internal` or `decrypt_with_proof`. Both should exist in the original participant set, normally `1..=n`, and invalid values should return an error or deterministic blame result. Replace indexing such as `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with checked lookups using `HashMap::get`, returning `DecryptionError`/`PedPoPError` instead of panicking.

### Proof of Concept

```rust
use frost::Participant;
use pedpop::{AdditionalBlameMachine, EncryptedMessage, SecretShare};
use dalek_ff_group::Ristretto;

// Machine constructed for a one-party DKG. It only registers participant 1.
let mut machine =
  AdditionalBlameMachine::<Ristretto>::new(context, 1, commitment_msgs_for_1)
    .expect("valid commitment messages");

// `recipient` is nonzero, so `Participant::new` accepts it, but it is outside 1..=n.
let sender = Participant::new(1).unwrap();
let recipient = Participant::new(2).unwrap();

// `msg` is attacker-supplied bytes read with EncryptedMessage::read and crafted
// so its embedded PoP verifies for `sender`.
machine.blame(sender, recipient, msg, Some(proof));
```

When the proof path executes, `decrypt_with_proof` attempts `self.enc_keys[&decryptor]` for participant `2` [5](#0-4) . Because `AdditionalBlameMachine::new` inserted only participant `1` [8](#0-7) , the map access panics.

### Citations

**File:** crypto/dkg/src/lib.rs (L29-35)
```rust
  pub const fn new(i: u16) -> Option<Participant> {
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L575-582)
```rust
  fn blame_internal(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) {
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-176)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-379)
```rust
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
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
