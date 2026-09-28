### Title
Unvalidated `sender`/`recipient` in PedPoP blame evaluation panics via `HashMap` indexing - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
Analogous to CVE-2021-27217 (insufficient validation of an untrusted field leading to a process crash), `BlameMachine::blame` / `AdditionalBlameMachine::blame` accept attacker-supplied `sender` and `recipient` `Participant` identifiers and index `self.commitments[&sender]` and `self.enc_keys[&decryptor]` without checking membership. Any accusation naming a participant outside the DKG set panics the running process — a denial of service reachable by an unprivileged party submitting public blame inputs.

### Finding Description
`blame` forwards `sender`/`recipient` directly into `blame_internal`, which calls `Decryption::decrypt_with_proof` and then evaluates `share_verification_statements` over `self.commitments[&sender]` at crypto/dkg/pedpop/src/lib.rs:599. `commitments` is only populated for `1 ..= n` in `AdditionalBlameMachine::new` (crypto/dkg/pedpop/src/lib.rs:656-659) and for actual DKG participants in `calculate_share`. Inside `decrypt_with_proof`, `self.enc_keys[&decryptor]` (crypto/dkg/pedpop/src/encryption.rs:388) performs the same unchecked indexing against a map populated only by `register`. Neither `blame` signature nor its docs require `sender`/`recipient` to be members of the participant set — only that `msg` be authenticated. Passing `sender = Participant(n + 1)` or any `recipient` that never registered produces a `HashMap` index panic rather than a `PedPoPError`.

### Impact Explanation
A single crafted blame/accusation message aborts the host process handling DKG blame adjudication. Since `AdditionalBlameMachine::new` explicitly supports third parties ("capable of evaluating Blame regardless of if the caller was a member"), this panic is reachable by non-participant evaluators and by any participant accusing a non-existent sender — a client-side DoS matching the CVE's class.

### Likelihood Explanation
Requires only that an attacker cause a blame evaluation referencing an out-of-set participant (e.g., a malformed accusation, or a participant index from a different/older set). No cryptographic work is needed; the panic occurs on first indexing. Medium, per the mirrored severity.

### Recommendation
Replace `self.commitments[&sender]` and `self.enc_keys[&decryptor]` with `.get()` returning `PedPoPError::MissingParticipant`/a blame-arbitration error, or validate `sender <= n && recipient <= n` up front in `blame`/`blame_internal`.

### Proof of Concept
```rust
// After a completed DKG producing BlameMachine (or via
// AdditionalBlameMachine::new(context, n, commitment_msgs)):
let attacker = Participant::new(n + 1).unwrap(); // not in the set
let forged: EncryptedMessage<C, SecretShare<C::F>> = /* any bytes */;
machine.blame(attacker, Participant::new(1).unwrap(), forged, None);
// panics at pedpop/src/lib.rs:599 on self.commitments[&sender]
```