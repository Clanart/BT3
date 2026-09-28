### Title
PedPoP blame evaluation indexes `enc_keys`/`commitments` by unvalidated participant indexes, panicking on missing entries (NULL-deref analog) - (File: crypto/dkg/pedpop/src/encryption.rs, crypto/dkg/pedpop/src/lib.rs)

### Summary
The ovpn bug class — dereferencing a slot before checking it is populated — maps directly onto Serai's PedPoP blame path. `BlameMachine::blame` / `AdditionalBlameMachine::blame` accept caller-supplied `sender` and `recipient` `Participant` indexes and pass them into `Decryption::decrypt_with_proof` and `blame_internal`, which index `self.enc_keys[&decryptor]` (encryption.rs:388) and `self.commitments[&sender]` (lib.rs:599) via `HashMap`'s `Index` impl. `std::collections::HashMap`'s `Index` panics when the key is absent. `Participant::new` only rejects zero, so any nonzero index `> n` (or any index not present in the map) is a valid `Participant` yet absent from these maps — the lookup is performed before any membership check, exactly mirroring `ovpn_crypto_kill_key` dereferencing a slot before checking it.

### Finding Description
- `Decryption::register` populates `enc_keys` only for participants `1..=n` whose commitment messages were provided (`AdditionalBlameMachine::new` iterates `1..=n`, erroring on missing entries; `verify_r1` registers only `all_participant_indexes`). [1](#0-0) 
- `decrypt_with_proof` evaluates the Schnorr PoP and then, when `proof` is `Some`, dereferences `self.enc_keys[&decryptor]` inside the DLEq verification point list without a `contains_key` check. [2](#0-1) 
- `blame_internal` later dereferences `self.commitments[&sender]` to evaluate share-verification statements, again with no membership check. [3](#0-2) 
- The public entry points `BlameMachine::blame` and `AdditionalBlameMachine::blame` take `sender`/`recipient` verbatim from an accusation; `AdditionalBlameMachine` is even documented as usable by a non-participant observer evaluating blame. [4](#0-3) 
- `Participant` is a bare `u16` newtype; `Participant::new` only rejects `0`, so `Participant(n+1)` is constructible and unauthenticated-blame inputs are attacker-influenced. [5](#0-4) 

Reachable paths:
1. Accusation `(sender, recipient)` where `recipient` is a valid `Participant` not in `1..=n` and `proof = Some(...)` → panic at `self.enc_keys[&decryptor]` (encryption.rs:388).
2. If the decrypt path is reached with `sender ∉ 1..=n` (e.g., after a successful `decrypt_with_proof`), panic at `self.commitments[&sender]` (lib.rs:599).
3. `Decryption::register` also `assert!`s on re-registration (encryption.rs:356-359); `AdditionalBlameMachine::new` removes entries before registering so it's internally consistent, but any reuse path passing the same participant twice panics.

### Impact Explanation
An unprivileged party (or a party relaying a blame accusation naming an out-of-range participant index) can deterministically panic any processor evaluating a PedPoP blame proof. In Serai, blame evaluation runs on validator processors adjudicating DKG faults; a panic aborts the blame-determination routine and, depending on the runtime, crashes the task/process — a remote denial of service analogous in class and reachability to the ovpn NULL dereference (availability-only, no key leakage). Because `blame`/`AdditionalBlameMachine` are designed to be callable by third parties to arbitrate disputes, the attacker needs only to cause an accusation message referencing a non-member index to be evaluated.

### Likelihood Explanation
Moderate. `Participant` values in blame accusations are not range-checked against `params.n()` anywhere in the blame path — neither `blame`, `blame_internal`, nor `decrypt_with_proof` validates `sender`/`recipient` membership. The fix in the kernel analog (read slot, check NULL, only proceed on match) translates directly: these maps must be queried with `get` and a missing entry turned into an error rather than a panic. The only mitigations are that the panic requires reaching the blame path (post-DKG, only when faults are alleged) and that a `Some(proof)` or a fully-decryptable message is needed for the respective deref.

### Recommendation
- In `Decryption::decrypt_with_proof`, replace `self.enc_keys[&decryptor]` with `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` (or a new `UnknownParticipant` error).
- In `blame_internal`, fetch `self.commitments.get(&sender)` and return a defined `Participant`/error result instead of indexing; also bounds-check `sender`/`recipient` against `1..=n` at the `blame` entry points.
- Reject `Participant` indexes `> n` in `BlameMachine::blame`/`AdditionalBlameMachine::blame` before any map access.

### Proof of Concept
```rust
// Setup: construct an AdditionalBlameMachine for an n-participant DKG.
let n: u16 = 3;
let mut commitment_msgs = HashMap::new();
for i in 1 ..= n {
  // ... populate authentic EncryptionKeyMessage<C, Commitments<C>> per participant
}
let machine = AdditionalBlameMachine::<C>::new(context, n, commitment_msgs).unwrap();

// Attacker supplies a blame accusation naming a recipient index that was never
// registered (n + 1 is a valid non-zero Participant but not in 1..=n).
let bogus_recipient = Participant::new(n + 1).unwrap();
let msg = /* any EncryptedMessage<C, SecretShare<C::F>> read from attacker bytes */;
let proof = Some(/* attacker-supplied EncryptionKeyProof<C> */);

// Panics at crypto/dkg/pedpop/src/encryption.rs:388 on `self.enc_keys[&decryptor]`
// (HashMap Index -> "key not found") before any validity verdict is produced.
machine.blame(Participant::new(1).unwrap(), bogus_recipient, msg, proof);
```

Second trigger, once decryption succeeds: `machine.blame(sender_out_of_range, honest_recipient, msg, proof)` reaches `self.commitments[&sender]` at `crypto/dkg/pedpop/src/lib.rs:599` and panics identically.

Caveat: I did not fully trace every upstream caller that filters `sender`/`recipient` before invoking `blame`; if an outer layer (outside the in-scope crates) already range-checks these indexes, reachability narrows to direct library consumers — but within the PedPoP crate itself, no such check exists.

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-391)
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

**File:** crypto/dkg/pedpop/src/lib.rs (L575-604)
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

**File:** crypto/dkg/src/lib.rs (L44-47)
```rust
impl From<Participant> for u16 {
  fn from(participant: Participant) -> u16 {
    participant.0
  }
```
