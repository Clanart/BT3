### Title
Unvalidated participant index causes panic (DoS) in PedPoP blame/decryption path - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The bug class from CVE-2018-8799 — an out-of-bounds access triggered by an attacker-supplied index inside message processing — maps onto PedPoP's blame machinery, where a `Participant` index carried in a blame/accusation flow is used directly as a `HashMap` key without checking it against `n`. Indexing a `HashMap` with `map[&key]` panics when the key is absent, so a syntactically valid (nonzero `u16`) but out-of-range participant index crashes the node processing the blame.

### Finding Description
`Decryption::decrypt_with_proof` looks up the accused recipient's registered encryption key via `self.enc_keys[&decryptor]` with no bounds check. `enc_keys` only contains entries for participants `1..=n` (populated via `Decryption::register` during `verify_r1`), but `decryptor` is a caller/message-supplied `Participant`, which is only guaranteed nonzero by `Participant::new` — not `<= n`. Any `Participant(x)` with `x > n` (or any index never registered) hits a missing key and panics. [1](#0-0) [2](#0-1) 

The same unchecked indexing exists in `BlameMachine::blame_internal`, which indexes `self.commitments[&sender]` when evaluating the share-verification statements — again, `sender` is only validated as a nonzero `u16`, while `commitments` only covers `1..=n`. [3](#0-2) 

Note that the earlier `if let Some(proof)` gate doesn't help: `blame`/`blame_internal` accept a `proof: Option<EncryptionKeyProof<C>>` and reach `self.enc_keys[&decryptor]` whenever `Some` proof is supplied, before any check that `decryptor` is a real participant.

### Impact Explanation
An unprivileged DKG participant (or anyone able to route a blame/accusation message to a node running this library) can crash the victim's key-generation process by triggering `blame`/`blame_internal` with a `sender`/`recipient`/`decryptor` index that is a valid `Participant` (`!= 0`) but `> n`. This is a direct analog of the rdesktop OOB-read segfault: an attacker-controlled index flows into an unchecked memory/table access, yielding a denial of service. In a threshold network, panicking honest processors mid-DKG aborts key generation; repeated triggers can keep a validator set unable to rotate/generate keys. Severity: Medium-High (remote, unauthenticated-by-design protocol message → process crash).

### Likelihood Explanation
Triggering requires only a malformed index field in the blame path — no valid cryptography, no collusion, no threshold of attackers. The cost is minimal. The main caveat is that `blame` is invoked by the integrator with the accused/accuser indexes; whether attacker-controlled indexes reach it depends on the calling layer (the production handler deserializes accuser/accused via `Participant::new(u16)`, which accepts any nonzero index without comparing to `n`). Because `Participant` carries no upper bound, the panic is structurally reachable wherever message-derived indexes are passed in.

### Recommendation
In `blame_internal` and `decrypt_with_proof`, validate `sender`, `recipient`, and `decryptor` against `params.n()` (or check `enc_keys`/`commitments` membership with `.get()`) and return the accusing/blamed party as faulty — or a dedicated `InvalidParticipant` error — instead of indexing with `map[&key]`. As defense-in-depth, any deserialization of a `Participant` intended to identify a protocol member should range-check `<= n` at parse time.

### Proof of Concept
Conceptual, assuming `n = 5`:

```rust
// `machine` is a BlameMachine<Ristretto> obtained via calculate_share for a
// 3-of-5 PedPoP session. `msg` is any EncryptedMessage<C, SecretShare<C::F>>
// (e.g., a real intercepted share message or one built with
// EncryptedMessage::read over attacker bytes). `proof` is Some(...) so the
// enc_keys lookup is reached.
let out_of_range = Participant::new(7).unwrap(); // valid Participant, but > n

// Path 1: enc_keys[&decryptor] panics (encryption.rs:388)
machine.blame_internal(sender, /* recipient/decryptor = */ out_of_range, msg, Some(proof));

// Path 2: commitments[&sender] panics (lib.rs:599)
// pass a valid `proof` and any `msg` whose PoP verifies for `sender`;
// a Participant(7) as `sender` reaches self.commitments[&sender] which
// only holds keys for 1..=5 → HashMap index panic → abort/DoS.
```

In both cases the panic is `HashMap`'s "no entry found for key" inside `blame_internal`/`decrypt_with_proof`, crashing the thread handling the blame message — analogous to the segfault caused by the OOB read in `process_secondary_order()`.

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

**File:** crypto/dkg/pedpop/src/lib.rs (L596-604)
```rust
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
