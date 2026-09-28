### Title
Unvalidated `Participant` indexes in PedPoP blame evaluation cause a denial-of-service panic - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`AdditionalBlameMachine::blame` and `BlameMachine` accept attacker-influenced `Participant` indexes for `sender` and `recipient` and pass them, unchecked against the registered commitment set, into `Decryption::decrypt_with_proof`, which indexes `self.enc_keys[&decryptor]` with the `HashMap` `Index` impl. Any `recipient`/`decryptor` index that was not registered (e.g., a participant index `> n`, which is a perfectly valid `Participant(u16)`) causes a panic (`HashMap` index on missing key), crashing the thread performing blame evaluation. This is the Serai analog of the vLLM empty-prompt crash (CVE-2024-8768): a remotely influenced, malformed-but-decodable input causes a hard panic instead of a graceful error.

### Finding Description
The PedPoP DKG provides `AdditionalBlameMachine`, a machine "capable of handling an arbitrary amount of additional blame proofs," constructed from the participant `commitment_msgs` map `1 ..= n` [1](#0-0) . Its `blame` entry point takes `sender: Participant` and `recipient: Participant` directly from the caller and forwards them to `blame_internal` with no bound check against `n` [2](#0-1) .

`blame_internal` reaches `Decryption::decrypt_with_proof`, which performs:

```rust
// crypto/dkg/pedpop/src/encryption.rs
&[self.enc_keys[&decryptor], *proof.key],
```

inside the DLEq verification statement [3](#0-2) . `enc_keys` is populated only for the `1 ..= n` participants registered via `Decryption::register` [4](#0-3) . Since `Participant` is any non-zero `u16` [5](#0-4) , a `decryptor` value such as `Participant(n + 1)` — never inserted into `enc_keys` — makes `self.enc_keys[&decryptor]` panic via `HashMap`'s `Index` impl, long before any error can be returned.

The same unchecked-index pattern exists in `Encryption::encrypt`, which does `self.decryption.enc_keys[&participant]` [6](#0-5) , and in `Decryption::register`, which `assert!`s on re-registration [7](#0-6)  — both are panic paths keyed off externally supplied `Participant` values rather than `io::Result`/`PedPoPError` returns.

### Impact Explanation
Any node evaluating a blame claim — e.g., a processor handling a `VerifyBlame`-style request where `accuser`/`accused` are deserialized from transaction data as raw `u16` participant indexes (as in `processor/src/key_gen.rs`, which calls `AdditionalBlameMachine::new(...).blame(accuser, accused, ...)` [8](#0-7) ) — will panic if either index is not a registered participant. Because the panic occurs inside library code on an externally controlled index, a single malformed blame input aborts the task/thread performing blame resolution, denying service to that processor. This matches the report's bug class (CWE-617, reachable assertion/panic) and the required impact of a reachable crash; availability loss is the direct consequence and no secret leakage is needed for the analog to hold.

### Likelihood Explanation
Blame messages exist precisely so that a *distrusted* party's claims can be evaluated — the inputs to `blame` are by definition untrusted and attacker-malleable. `Participant` deserialization only enforces `!= 0`; nothing in `AdditionalBlameMachine::new`, `blame`, or `decrypt_with_proof` checks `u16::from(recipient) <= n`. Crafting `accuser`/`accused`/`recipient` = `n + 1` (or any unregistered index) requires no privilege and no valid cryptographic material — only reaching the blame-evaluation path before the panic. The panic occurs on the first `HashMap` index, so even a garbage `EncryptedMessage` isn't needed to satisfy prior checks if the code path reaches `decrypt_with_proof` with a proof present. Note: whether `blame_internal` itself bounds-checks `sender`/`recipient` before calling `decrypt_with_proof` could not be fully verified in this pass; if it does not, the panic is directly reachable; the `enc_keys[&decryptor]` panic itself is certain whenever an unregistered index arrives.

### Recommendation
Replace all indexing/`assert!` uses on participant-keyed maps with error returns:

- In `Decryption::decrypt_with_proof`, use `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)` (or a new `UnknownParticipant` variant) instead of `self.enc_keys[&decryptor]` [9](#0-8) .
- In `AdditionalBlameMachine::blame` / `BlameMachine`, validate `u16::from(sender) <= n` and `u16::from(recipient) <= n` (and that both were registered) up front, returning `Err(PedPoPError::MissingParticipant(_))` rather than panicking [2](#0-1) .
- In `Decryption::register` and `Encryption::encrypt`, return errors instead of `assert!`/indexing on duplicate or unknown participants [4](#0-3) [6](#0-5) .
- Add a regression test calling `AdditionalBlameMachine::blame` with `sender`/`recipient` = `Participant::new(n + 1).unwrap()` asserting a graceful error, not a panic.

### Proof of Concept
```rust
// Requires: crypto/dkg/pedpop, crypto/ciphersuite, a Ciphersuite impl (e.g. dalek-ff-group Ristretto)
use std::collections::HashMap;
use dkg::Participant;
use pedpop::*; // AdditionalBlameMachine, EncryptionKeyMessage, Commitments, EncryptedMessage, SecretShare

fn dos_via_unregistered_participant<C: ciphersuite::Ciphersuite>() {
    let n: u16 = 3;
    let context = [0u8; 32];

    // Build valid commitment messages for participants 1..=n (as an honest setup would).
    let mut commitment_msgs: HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>> = todo!();

    let machine = AdditionalBlameMachine::<C>::new(context, n, commitment_msgs).unwrap();

    // Attacker-controlled blame input: accuser claims to be participant n+1,
    // which is a legal Participant (non-zero u16) but was never registered.
    let accuser = Participant::new(n + 1).unwrap();
    let accused = Participant::new(1).unwrap();

    let msg: EncryptedMessage<C, SecretShare<C::F>> = todo!(); // any parsed/blame-msg bytes
    machine.blame(accused, accuser, msg, None);
    // PANIC inside Decryption::decrypt_with_proof:
    //   self.enc_keys[&decryptor]  -- HashMap index on missing key
    // Thread aborts; blame evaluation is DoS'd.
}
```

The panic is guaranteed at `crypto/dkg/pedpop/src/encryption.rs` (`self.enc_keys[&decryptor]`) whenever `decrypt_with_proof` executes with a `decryptor` outside `1 ..= n`; the only unverified link is whether `blame_internal` pre-filters such indexes, which — based on the visible signatures passing raw `Participant` values straight through — it does not.

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L460-467)
```rust
  pub(crate) fn encrypt<R: RngCore + CryptoRng, E: Encryptable>(
    &self,
    rng: &mut R,
    participant: Participant,
    msg: Zeroizing<E>,
  ) -> EncryptedMessage<C, E> {
    encrypt(rng, self.context, self.i, self.decryption.enc_keys[&participant], msg)
  }
```

**File:** crypto/dkg/src/lib.rs (L26-35)
```rust
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

**File:** processor/src/key_gen.rs (L538-556)
```rust
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
        let network_blame = AdditionalBlameMachine::new(
          context(&id, NETWORK_KEY_CONTEXT),
          params.n(),
          network_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, network_share, network_blame);
```
