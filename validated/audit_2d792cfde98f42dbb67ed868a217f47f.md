### Title
Unvalidated `Participant` indexes in PedPoP blame evaluation cause a denial of service (panic on HashMap indexing) - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
Analogous to CVE-2016-10147 (requesting an incompatible algorithm dereferences a missing object and crashes the system), `BlameMachine::blame` / `AdditionalBlameMachine::blame` index `self.commitments[&sender]` and `self.enc_keys[&decryptor]` without verifying that `sender`/`recipient` are in `1..=n`. `Participant::new` only rejects zero (`crypto/dkg/src/lib.rs:600` deserialization path), so any unbounded `u16` index is a legal `Participant`. A peer submitting an accusation naming a non-existent participant (e.g. index `n+1`) triggers a panic — the Rust equivalent of a NULL-deref crash — killing the DKG/blame evaluation context.

### Finding Description
`BlameMachine::blame` forwards to `blame_internal`, which does two unchecked lookups:

1. `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` indexes `self.enc_keys[&decryptor]` inside `Decryption::decrypt_with_proof` (`crypto/dkg/pedpop/src/encryption.rs:388`). `enc_keys` only contains entries for participants `1..=n` registered via `Decryption::register` (`encryption.rs:351-361`).
2. `self.commitments[&sender]` (`crypto/dkg/pedpop/src/lib.rs:599`) — `commitments` is likewise keyed strictly by `1..=n` (populated in `verify_r1` / `AdditionalBlameMachine::new`).

Both are `HashMap` index operations (`map[&key]`), which panic on a missing key. Neither `blame` nor `blame_internal` nor `AdditionalBlameMachine::new` bounds-check `sender` or `recipient`, and `Participant` carries no upper bound (only non-zero, enforced in `Participant::new`). The docs for `AdditionalBlameMachine::new` only warn about *invalid commitment messages*, not about out-of-range participant indexes at the `blame` callsite — the panic is undocumented and reachable.

Reachability: blame evaluation is precisely the API exposed for adjudicating accusations between parties (`blame` "determine[s] the faulty party ... given an accusation of fault"). An accuser supplies `sender`, `recipient`, the message, and the proof — all untrusted inputs — so an accusation naming `recipient = Participant(n + 1)` or `sender = Participant(0xffff)` reaches both indexing sites.

### Impact Explanation
An unprivileged party can crash any node evaluating a blame accusation by naming a participant index outside `1..=n`. This aborts the DKG completion/blame flow and, depending on the host (panic = process abort or dropped async task), denies service of the threshold-signing participant — matching the CVE's local-crash / availability-impact class (CVSS 5.5, `A:H`).

### Likelihood Explanation
Requires only the ability to submit a blame accusation (or any input controlling `sender`/`recipient`) to a party running `BlameMachine::blame`/`AdditionalBlameMachine::blame`. No valid keys, valid proofs, or cryptographic work are needed: the panic occurs during argument resolution, before proof verification for the `recipient` path (the `enc_keys[&decryptor]` lookup happens inside `decrypt_with_proof`, though only after the sender's PoP signature verifies — so the `sender` path via `commitments[&sender]` at `lib.rs:599` requires passing the PoP check, while the `decryptor` lookup requires a well-formed PoP on `msg`). The most reliable trigger is `sender` out-of-range combined with a `msg` whose PoP fails: `blame_internal` returns early at `DecryptionError::InvalidSignature` only *after* `msg.pop.verify` fails — so a forged PoP-less message returns `sender` without panicking. The panic fires when `msg` carries a *valid* PoP but `recipient` is out of range (`enc_keys[&decryptor]`), or when `recipient` is valid and `sender` is out of range after a valid PoP (`commitments[&sender]`). Either way the attacker needs one validly-formed `EncryptedMessage` PoP, which they can produce by encrypting under their own ephemeral key — fully within public-input reach.

### Recommendation
Bounds-check `sender` and `recipient` against `1..=n` at the top of `blame_internal` (e.g. `sender.0 <= n && recipient.0 <= n`, else return the accusing party or a dedicated `PedPoPError::InvalidParticipant`), and/or replace `map[&k]` indexing with `map.get(&k).ok_or(...)` in `decrypt_with_proof` and `blame_internal` so an unknown participant index yields an error instead of a panic.

### Proof of Concept
```rust
// n = 3 DKG completes; blame is then evaluated over attacker-controlled accusation args
let machine = AdditionalBlameMachine::<Ristretto>::new(context, 3, commitment_msgs).unwrap();

// Build an EncryptedMessage whose PoP verifies (attacker knows the ephemeral key),
// then name a recipient index that was never registered.
let bogus_recipient = Participant::new(4).unwrap(); // n = 3
machine.blame(sender, bogus_recipient, msg, Some(proof));
// -> decrypt_with_proof: self.enc_keys[&bogus_recipient] panics (HashMap index miss)
//    at crypto/dkg/pedpop/src/encryption.rs:388

// Alternatively, a validly-PoP'd msg with sender = Participant(200) panics at
// self.commitments[&sender] in crypto/dkg/pedpop/src/lib.rs:599
```

Relevant code: `Participant` has no upper bound (`crypto/dkg/src/lib.rs:123`); `Decryption::register` only populates `enc_keys` for actual participants (`crypto/dkg/pedpop/src/encryption.rs:351-361`); the unchecked lookups are at `crypto/dkg/pedpop/src/encryption.rs:388` (`self.enc_keys[&decryptor]`) and `crypto/dkg/pedpop/src/lib.rs:599` (`self.commitments[&sender]`).