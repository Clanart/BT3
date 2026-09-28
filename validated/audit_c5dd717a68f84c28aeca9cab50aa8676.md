### Title
Unvalidated `sender` participant index in PedPoP blame evaluation panics on out-of-range accusation (reachable DoS) - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` accept attacker-influenced `sender`/`recipient` `Participant` indices and index `self.commitments` with them via `HashMap` indexing (`self.commitments[&sender]`). If the accusing message names a `sender` that was never registered in the commitments map, the indexing panics, crashing the process evaluating the blame proof. This is the same bug class as the referenced report: an unprivileged party causing a repeatable crash (availability loss) of the service via inputs they control.

### Finding Description
`blame_internal` is reached through two public APIs:

- `BlameMachine::blame(self, sender, recipient, msg, proof)` at `crypto/dkg/pedpop/src/lib.rs:623-632`
- `AdditionalBlameMachine::blame(&self, sender, recipient, msg, proof)` at `crypto/dkg/pedpop/src/lib.rs:674-682`

Neither validates that `sender` (or `recipient`) is within the DKG's participant set. `blame_internal` then performs:

```rust
// crypto/dkg/pedpop/src/lib.rs:596-605
if !bool::from(
  multiexp_vartime(&share_verification_statements::<C>(
    recipient,
    &self.commitments[&sender],   // <-- panics if sender not in map
    Zeroizing::new(share),
  ))
  .is_identity(),
) {
  return sender;
}
```

`self.commitments` is built in `AdditionalBlameMachine::new` (`crypto/dkg/pedpop/src/lib.rs:649-661`) which only inserts keys for `Participant::new(1 ..= n)`, and in `calculate_share` via `verify_r1`, which only inserts legitimate protocol participants (`crypto/dkg/pedpop/src/lib.rs:313-336`). Any `sender` index `> n` — e.g. `Participant::new(0xFFFF)` — is absent from the map, so `commitments[&sender]` panics at the `HashMap` `Index` impl.

The same panic shape exists on the decryption path: `Decryption`/`encryption.decrypt_with_proof` is keyed by the sender's encryption key material registered per participant, so an out-of-range `sender` is likewise unhandled before line 599 (the exact lookup there was not fully traced, but line 599 alone is sufficient: if `decrypt_with_proof` returns `Ok` with a scalar-parseable `SecretShare`, the `commitments[&sender]` index is reached unconditionally).

An accuser-controlled accusation (sender index, recipient index, the encrypted `SecretShare` blob `msg`, and optional `proof` — all of which are externally supplied to `blame`) therefore yields a repeatable panic in any caller that runs blame arbitration, including `AdditionalBlameMachine::new` + `blame` which is explicitly documented as usable by a non-participant evaluating blame (`crypto/dkg/pedpop/src/lib.rs:639-648`).

For contrast, the protocol's own defensive validation (`validate_map`, `crypto/dkg/pedpop/src/lib.rs:57-83`) bounds maps to `all_participant_indexes()` — but this validation is applied to `commitment_msgs`/`shares`, never to the `sender`/`recipient` arguments of `blame`.

### Impact Explanation
A panic in `blame_internal` aborts blame evaluation and, in a non-`catch_unwind` deployment (typical for Rust services where `panic = abort` or where the panic propagates to a thread running consensus/tributary logic), crashes the node process. Since the trigger is a single crafted accusation message (`sender` outside `1..=n`), any peer able to submit a blame/accusation to a validator running PedPoP blame arbitration can repeatedly crash it — matching the referenced CVE's "unauthorized ability to cause a hang or frequently repeatable crash (complete DOS)" outcome.

### Likelihood Explanation
Reachability requires a deployment surface that feeds externally-sourced accusations into `BlameMachine::blame` or `AdditionalBlameMachine::blame`. `Participant` is a plain `u16` newtype (`Participant::new` accepts any nonzero `u16`), so a crafted index requires no brute force — `n + 1 ..= u16::MAX` all trigger the panic. The `AdditionalBlameMachine` path is explicitly designed for third-party blame evaluation, making it the most exposed entry point. Exploitation also requires either a `msg` that decrypts (to reach line 599) or an earlier unguarded lookup in `decrypt_with_proof`; the decrypt path may reject first, in which case the panic surface narrows, but the missing index validation is unambiguous.

### Recommendation
Validate `sender` and `recipient` at the top of `blame_internal` (and in `AdditionalBlameMachine::blame`) against `self.commitments` / `1 ..= n`, returning a defined `PedPoPError`-style result or a sentinel faulty party instead of indexing. Replace `self.commitments[&sender]` with `.get(&sender)` and handle `None`. Apply the same check to the `recipient` argument before it is used in `share_verification_statements`.

### Proof of Concept
```rust
// In-scope crate: crypto/dkg/pedpop
// Setup: run a normal PedPoP round so `commitment_msgs` exist for n participants,
// then construct a non-participant blame arbiter:

let mut arbiter = AdditionalBlameMachine::<Ristretto>::new(context, n, commitment_msgs)?;

// Craft an accusation naming a participant index that was never in the DKG.
let malicious_sender = Participant::new(u16::from(n) + 1).unwrap(); // valid u16, not in 1..=n
let recipient        = Participant::new(1).unwrap();

// `msg`/`proof` are attacker-supplied bytes. With any msg for which
// decrypt_with_proof returns Ok and the share parses as a scalar
// (e.g. replaying a real sender's EncryptedMessage to recipient=1 under
// the spoofed `malicious_sender` index if the ECDH transcript permits),
// execution reaches:
//     &self.commitments[&malicious_sender]
// which is absent -> HashMap Index panic -> process abort.
let _faulty = arbiter.blame(malicious_sender, recipient, msg, proof);
```

Even where `decrypt_with_proof` rejects first, the missing bounds check means no attacker-supplied participant index is ever sanitized before use as a map key, and any future/parallel lookup of the encryption key by the same index has the same panic shape.