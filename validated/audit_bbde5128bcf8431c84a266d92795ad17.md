### Title
Out-of-range participant index in a PedPoP blame accusation causes a panic (denial of service) - ([File: crypto/dkg/pedpop/src/lib.rs])

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` trust the attacker-influenced `sender` and `recipient` `Participant` indexes and index directly into `self.commitments` and `self.encryption.enc_keys` with the `HashMap` `[]` operator. Since `Participant` only enforces non-zero — not `<= n` — an accusation naming any `sender`/`recipient` index outside `1..=n` panics, crashing the process evaluating blame. This mirrors the CVE-2017-3244 bug class: a low-privilege network peer causes a reliable crash/hang (complete availability loss) with a trivially crafted protocol message.

### Finding Description
`Participant::new` only rejects `0`, so any `u16` in `1..=u16::MAX` is a "valid" `Participant` (crypto/dkg/src/lib.rs:29-35). The blame-evaluation path then uses those unchecked indexes:

- `blame` → `blame_internal(sender, recipient, msg, proof)` at crypto/dkg/pedpop/src/lib.rs:623-632.
- `blame_internal` calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` at lib.rs:582, which indexes `self.enc_keys[&decryptor]` at crypto/dkg/pedpop/src/encryption.rs:388 — panics if `recipient` was not registered (i.e., `> n`).
- If a `proof` is omitted (`InvalidSignature` path returns early only if the PoP fails), or if the proof path is skipped, `blame_internal` proceeds to `&self.commitments[&sender]` at lib.rs:599 — panics if `sender > n`.

`Decryption::new`/`register` only populate `enc_keys` for real participants (encryption.rs:356-362, `AdditionalBlameMachine::new` populates `1..=n` at lib.rs:656-660), and `commitments` likewise only contains `1..=n`. So `self.enc_keys[&decryptor]` and `self.commitments[&sender]` are guaranteed to panic for any accusation whose `recipient`/`sender` is a well-formed `Participant` (> 0) but out of range (> n) — exactly the kind of index the caller forwards from a peer's blame message, which is built from untrusted bytes read via `EncryptedMessage::read` / `EncryptionKeyProof::read` and a `Participant` the accuser names.

Notably, `calculate_share` carefully validates its map with `validate_map` (lib.rs:468-472), but `blame`/`blame_internal` perform no equivalent bounds check on `sender`/`recipient` before the `HashMap` index operations.

### Impact Explanation
A participant (or anyone able to get an accusation routed to a `BlameMachine`/`AdditionalBlameMachine`) sends a blame request naming a `sender` or `recipient` index greater than `n`. The victim's node panics at the `HashMap` index, killing the DKG/blame-handling task — a repeatable, remotely-triggerable crash (availability impact), matching the medium-severity DoS class of the reference CVE.

### Likelihood Explanation
Triggering requires only sending a blame/accusation message with an out-of-range participant index — no valid proofs, keys, or threshold collusion needed. `AdditionalBlameMachine::blame` is documented as callable to evaluate blame even by a non-member, and its inputs (`sender`, `recipient`, `EncryptedMessage`, `proof`) are all attacker-influenced. The panic is deterministic, not probabilistic.

### Recommendation
Bounds-check `sender` and `recipient` in `blame`/`blame_internal` against the registered `commitments`/`enc_keys` maps (or against `params`/`n`) before indexing — e.g., return the accuser (or a defined error) when either index is not present, replacing `map[&k]` with `map.get(&k)` and a graceful fault attribution for malformed accusations.

### Proof of Concept
```rust
// Setup: n = 4 DKG; honest node built AdditionalBlameMachine via
// AdditionalBlameMachine::new(context, 4, commitment_msgs)
// Attacker then submits a blame accusation with sender = Participant::new(5) (or recipient = 5):

let sender = Participant::new(5).unwrap();     // valid (non-zero) but > n
let recipient = Participant::new(2).unwrap();
// msg is any EncryptedMessage::<C, SecretShare<C::F>>::read output,
// proof = None, crafted so pop.verify fails OR reaches the commitments lookup.
machine.blame(sender, recipient, msg, None);
// panic: 'no entry found for key' at
//   crypto/dkg/pedpop/src/encryption.rs:388  (self.enc_keys[&decryptor])
//   or crypto/dkg/pedpop/src/lib.rs:599      (self.commitments[&sender])
```
Since `Participant`'s only validity rule is non-zero (crypto/dkg/src/lib.rs:29-35), the accusation bytes deserialize cleanly and the crash is unconditional.