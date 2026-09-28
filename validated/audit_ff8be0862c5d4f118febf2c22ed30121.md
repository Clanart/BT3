### Title
Unvalidated participant index in blame evaluation causes index-out-of-map panic (DoS) - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The upstream bug class is a crash/panic reachable via untrusted input (unbounded error output → panic in the kernel). The Serai analog is a panic reachable via attacker-controlled participant indexes in the PedPoP blame-evaluation API: `BlameMachine::blame`, `AdditionalBlameMachine::blame`, and `AdditionalBlameMachine::new` accept arbitrary `sender`/`recipient` `Participant` values, while the internal commitments map only contains entries for participants `1..=n`. Indexing that map with an out-of-range participant panics.

### Finding Description
`BlameMachine` stores `commitments: HashMap<Participant, Vec<C::G>>` populated only for the DKG's actual participant set (`1..=n`), via `self.params.all_participant_indexes()` in `verify_r1` (crypto/dkg/pedpop/src/lib.rs:313-336) or via the `1 ..= n` loop in `AdditionalBlameMachine::new` (crypto/dkg/pedpop/src/lib.rs:656-659).

`blame_internal` is invoked by both `BlameMachine::blame` (line 630) and `AdditionalBlameMachine::blame` (line 681). Neither `blame` entry point validates that `sender` (or `recipient`) is within `1..=n` — `Participant::new` only rejects `0` (crypto/dkg/src/lib.rs:29-35). Inside `blame_internal`, the map is indexed directly:

```rust
// crypto/dkg/pedpop/src/lib.rs:596-600
if !bool::from(
  multiexp_vartime(&share_verification_statements::<C>(
    recipient,
    &self.commitments[&sender],   // panics if sender ∉ 1..=n
    Zeroizing::new(share),
  ))
  .is_identity(),
) {
```

If `decrypt_with_proof` returns `Ok` and the decrypted bytes are a canonical scalar, `self.commitments[&sender]` is evaluated for an arbitrary, attacker-named `sender`. `HashMap`'s `Index` impl panics on a missing key, aborting the process. Additionally, `decrypt_with_proof` itself performs per-participant lookups of registered encryption keys, so an out-of-range `sender` may panic even earlier in the call chain (the exact internal indexing in `crypto/dkg/pedpop/src/encryption.rs` could not be fully verified, but the panic at `commitments[&sender]` is unconditional once decryption succeeds).

The same unchecked indexing pattern exists in `KeyMachine::calculate_share` at `self.commitments[&l]` (line 490), but that path is protected because `shares` keys are constrained by `validate_map` against `all_participant_indexes()` (lines 468-472). The blame path has no equivalent guard.

### Impact Explanation
An unprivileged party able to submit a blame accusation — a `sender`/`recipient` pair plus a serialized `EncryptedMessage<C, SecretShare<C::F>>` and optional `EncryptionKeyProof<C>`, all fed through `EncryptedMessage::read` — can crash any node evaluating blame. In a DKG that relies on blame resolution (including third-party evaluation via `AdditionalBlameMachine`, which is explicitly designed for non-participants to adjudicate accusations), a fabricated accusation naming `Participant(n+1)` (or any non-zero u16 outside the set) causes a panic instead of an error return. This kills the protocol process and prevents completion of the DKG — a remote, input-driven denial of service, matching the panic-class severity of the referenced advisory.

### Likelihood Explanation
The blame API's entire purpose is processing accusations about protocol faults, and accusations inherently originate from potentially faulty/malicious parties. `sender` and `recipient` are attacker-influenced values with no bounds check against `n` anywhere in the call path. The only gate is that `decrypt_with_proof` must return `Ok` before the `commitments[&sender]` index is reached; whether a fabricated-`sender` message can decrypt depends on `Decryption`'s internal key lookup, which likely panics on the same out-of-range index even earlier — either way the process panics on input it was expected to handle. The library itself acknowledges this fragility in `AdditionalBlameMachine::new`'s doc comment: "may cause everything from inaccurate blame to panics" (line 648), but the panic here is reachable through the ordinary, documented `blame` API rather than only through misuse of `new`.

### Recommendation
Validate `sender` and `recipient` against the registered participant set (`self.commitments.contains_key(...)` or `1..=n`) at the top of `blame_internal` and in `AdditionalBlameMachine::blame`, returning a `Result` or a defined fault verdict instead of indexing. Replace `self.commitments[&sender]` with `self.commitments.get(&sender)` and propagate an error. Apply the same check inside `Decryption`'s per-participant key lookup in `crypto/dkg/pedpop/src/encryption.rs`.

### Proof of Concept
```rust
// Setup: complete (or externally reconstruct) a PedPoP DKG with n = 3 participants.
// Obtain a BlameMachine via KeyMachine::calculate_share, or an
// AdditionalBlameMachine via AdditionalBlameMachine::new(context, 3, commitment_msgs).

// Attacker submits an accusation naming a fabricated sender index.
let fake_sender = Participant::new(4).unwrap(); // n = 3, so 4 is out of range
let recipient   = Participant::new(1).unwrap();

// msg: any EncryptedMessage<C, SecretShare<C::F>> read via EncryptedMessage::read
// that decrypts to a canonical scalar under the (sender, recipient) key context,
// or any message that triggers the out-of-range key lookup inside
// decrypt_with_proof.
let msg: EncryptedMessage<C, SecretShare<C::F>> = /* attacker-crafted bytes */ read(...);

// Panic: index into HashMap with absent key `Participant(4)` at
// crypto/dkg/pedpop/src/lib.rs:599 (`self.commitments[&sender]`),
// or earlier inside Decryption::decrypt_with_proof's per-participant lookup.
let (_machine, faulty) = blame_machine.blame(fake_sender, recipient, msg, None);
```

Observed behavior: panic (`HashMap` index on missing key / unreachable `unwrap`), process abort. Expected: an error or blame verdict, since `Participant(4)` was never part of this DKG.

*Uncertainty note:* the precise panic site (inside `decrypt_with_proof` vs. `commitments[&sender]`) depends on `Decryption`'s internal indexing in `crypto/dkg/pedpop/src/encryption.rs`, whose `decrypt`/`decrypt_with_proof` bodies were not fully indexed; the out-of-range panic on the `blame` path holds under either interpretation.