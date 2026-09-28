### Title
Unauthenticated panic in PedPoP blame evaluation via out-of-set `sender`/`recipient` participant index - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` take `sender` and `recipient` `Participant` indexes and look them up directly in the `commitments` `HashMap` with indexing syntax. `Participant` only guarantees a non-zero `u16`; it is not bounded by the DKG set size `n`. Any accusation naming a participant not in `1 ..= n` causes a panic instead of an error, aborting the calling process. This mirrors the CVE-2025-21531 class: a remotely triggerable, repeatable crash (DoS) reached through a public protocol input.

### Finding Description
In `blame_internal`, the commitments map is indexed unconditionally:

- `crypto/dkg/pedpop/src/lib.rs:599` — `&self.commitments[&sender]` inside `share_verification_statements` evaluation.
- The same map is populated only for `1 ..= n` (`AdditionalBlameMachine::new`, lines 656–659; `calculate_share`, lines 511–521).

`sender` and `recipient` are caller-supplied `Participant` values. `Participant::new` rejects only `0`, so any value `> n` is accepted by the type and reaches the `HashMap` index, which panics on a missing key (`HashMap`'s `Index` impl). Additionally, `encryption.decrypt_with_proof(sender, recipient, ...)` in `crypto/dkg/pedpop/src/encryption.rs` receives the same unvalidated indexes before the commitments lookup, providing a second potential panic path.

Reachability: `blame()` is a public API intended to process accusations of fault between protocol participants. The `msg`/`proof` arguments are deserialized via `EncryptedMessage::read` / `EncryptionKeyProof::read` from bytes supplied by the accusing/accused parties, and the participant indexes are taken from the accusation itself — i.e., unauthenticated public inputs. No malformed bytes or leaked keys are needed; a well-formed accusation naming `Participant(n+1)` suffices.

### Impact Explanation
A single crafted blame request crashes the node evaluating it (panic across the `blame` call, unwinding or aborting the host). Since blame evaluation is the mechanism used to keep the DKG honest, crashing it is a complete availability loss for that node's key-generation session and, depending on the caller's panic handling, for the whole process. Repeatable by any party able to submit an accusation, matching "unauthorized ability to cause a hang or frequently repeatable crash" at Medium severity.

### Likelihood Explanation
Any participant (or external evaluator using `AdditionalBlameMachine::new`, which explicitly supports non-participant callers, lines 639–649) can invoke `blame` with arbitrary `sender`/`recipient` values. The code documents that invalid *commitments* are undefined behavior, but places no such caveat on the participant indexes of an accusation — the entire purpose of `blame` is to adjudicate claims about faulty parties, so out-of-range or bogus accusations are expected adversarial input, not documented-MUST misuse.

### Recommendation
Replace `self.commitments[&sender]` / `[&recipient]` indexing with `.get()` and return an explicit `PedPoPError` (e.g. `InvalidParticipant`) when the index is not a member of the DKG set. Apply the same bounds check at the top of `BlameMachine::blame`, `AdditionalBlameMachine::blame`, and inside `Decryption::decrypt_with_proof` before any map lookups.

### Proof of Concept
```rust
// Given params n = 3, t = 2, and a completed AdditionalBlameMachine
// built via AdditionalBlameMachine::new(context, 3, commitment_msgs)
let out_of_set = Participant::new(4).unwrap(); // accepted: non-zero u16
let member = Participant::new(1).unwrap();

// Any syntactically valid EncryptedMessage/EncryptionKeyProof bytes
let msg = EncryptedMessage::<C, SecretShare<C::F>>::read(&mut bytes, params).unwrap();

// Panics at crypto/dkg/pedpop/src/lib.rs:599 on self.commitments[&out_of_set]
machine.blame(out_of_set, member, msg, None);
```
Expected: `Result`/blame outcome identifying the invalid accusation. Actual: thread panic (`HashMap` index on missing key), crashing the evaluating node. Note: if `decrypt_with_proof` indexes its key store first, the same input may panic even earlier — either path confirms the DoS.