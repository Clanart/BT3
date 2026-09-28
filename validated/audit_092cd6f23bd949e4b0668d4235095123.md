### Title
Unvalidated `sender`/`recipient` Participant index panics `BlameMachine::blame_internal` via `HashMap` indexing - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The PedPoP blame-evaluation API indexes a participant-keyed `HashMap` with attacker-controlled `Participant` values without validating them against the registered participant set. An invalid index panics the calling task, mirroring the PX4 bug class: an attacker-supplied length/index used without bounds to touch memory outside the valid set, crashing the task.

### Finding Description
`BlameMachine::blame` and `AdditionalBlameMachine::blame` (public APIs in `crypto/dkg/pedpop`) take `sender: Participant` and `recipient: Participant` straight from untrusted input and pass them to `blame_internal` at `crypto/dkg/pedpop/src/lib.rs:575`. There, after decryption succeeds, the code performs `&self.commitments[&sender]` at `crypto/dkg/pedpop/src/lib.rs:599` — a `HashMap` index that panics if `sender` was not inserted during `AdditionalBlameMachine::new` (which only inserts participants `1..=n` at `crypto/dkg/pedpop/src/lib.rs:656-660`) or the `Commitments` map built in `verify_r1` (`crypto/dkg/pedpop/src/lib.rs:313-336`).

No bounds check is applied to `sender` or `recipient` anywhere in `blame`/`blame_internal`. `Participant::new` only rejects zero, so any `Participant` value `> n` is accepted as a parameter. The panic is reachable: an attacker controlling the `msg: EncryptedMessage` and `proof: Option<EncryptionKeyProof>` inputs can craft a message signed/encrypted under a key they know, binding the PoP to any `from` participant value (the `pop_challenge` binds `from` but the attacker computes it themselves, `crypto/dkg/pedpop/src/encryption.rs:164`), plus a valid `EncryptionKeyProof` DLEq for the ECDH key. With `from_repr` succeeding on a canonical scalar share, execution reaches `self.commitments[&sender]` with `sender` absent and panics. In the deployed coordinator, `VerifyBlame` (`processor/src/key_gen.rs:504-549`) feeds `accuser`/`accused`/`share`/`blame` from a peer-supplied message into `AdditionalBlameMachine::blame`, giving an unprivileged peer this reach.

### Impact Explanation
Unauthenticated crash of the blame-verification path — a malicious participant (or any party able to submit blame, as `AdditionalBlameMachine` is explicitly designed for non-participants) aborts the process with an out-of-range participant index, equivalent to the PX4 driver stack overwrite crashing the task. Denial of service of DKG fault adjudication, and potentially of the validator process itself since the panic is not caught.

### Likelihood Explanation
Medium. Requires crafting a valid `EncryptedMessage` + `EncryptionKeyProof` for a `sender` outside `1..=n`, which any party capable of producing the Schnorr PoP and DLEq proof can do cheaply (they know the ephemeral scalar). Only reachable once the earlier `decrypt_with_proof` and `from_repr` checks pass, so it is a targeted rather than trivial crash, but fully within the attacker’s control.

### Recommendation
In `blame_internal` (and `BlameMachine::blame`/`AdditionalBlameMachine::blame`), validate `sender` and `recipient` against `self.commitments.keys()` (or `1..=n`) before indexing, and use `self.commitments.get(&sender)` returning a defined fault (e.g., blame the accuser/recipient or error) instead of `Index` on the map. `crypto/dkg/pedpop/src/lib.rs:599` should not panic on adversarial input.

### Proof of Concept
```rust
// Conceptual: any context/n where an AdditionalBlameMachine exists.
let machine = AdditionalBlameMachine::<Ristretto>::new(context, n, valid_commitment_msgs)?;
// Attacker crafts EncryptedMessage with pop_challenge bound to `from = sender`,
// where sender = Participant::new(n + 1) (any value not in 1..=n), plus a valid
// EncryptionKeyProof for the ECDH shared key, and a canonical scalar as `msg`.
machine.blame(
  Participant::new(n + 1).unwrap(), // sender: not a registered participant
  Participant::new(1).unwrap(),     // recipient
  crafted_msg,
  Some(valid_proof),
);
// After decryption succeeds and the share parses, line 599 executes
// `self.commitments[&sender]` with sender absent -> panic! (index out of bounds
// analog; crashes the task, matching the PX4 dev_name_len overflow crash class).
```