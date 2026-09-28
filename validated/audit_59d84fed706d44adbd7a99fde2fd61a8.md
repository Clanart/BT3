### Title
Denial of Service via unbounded index into participant maps during DKG blame evaluation - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to the PyTorch `torch.linalg.lu` slice-operation DoS (BIT-pytorch-2025-55551 / CVE-2025-55551), Serai's PedPoP DKG performs unchecked indexing into `HashMap<Participant, _>` structures with attacker-influenced participant indexes. `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` and `BlameMachine::blame_internal` indexes `self.commitments[&sender]`; Rust's `HashMap` `Index` impl panics on a missing key, crashing the evaluating node.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs`, `decrypt_with_proof` verifies the blame DLEq proof against `self.enc_keys[&decryptor]`:

```rust
proof.dleq.verify(
  &mut encryption_key_transcript(self.context),
  &[C::generator(), msg.key],
  &[self.enc_keys[&decryptor], *proof.key],
)
```

`enc_keys` is only populated by `Decryption::register`, which is invoked once per in-protocol participant index (`1 ..= n`) during `SecretShareMachine::verify_r1` / `AdditionalBlameMachine::new`. If `decryptor` is any `Participant` value not in `1 ..= n` (e.g., `Participant(n + 1)` up to `u16::MAX`), the indexing panics before any verification result is produced.

The same pattern exists in `crypto/dkg/pedpop/src/lib.rs` `blame_internal`, which evaluates `share_verification_statements` over `self.commitments[&sender]` — again panicking for any `sender` index that was not a registered DKG participant.

Both are reached through the public blame APIs:

- `BlameMachine::blame(sender, recipient, msg, proof)`
- `AdditionalBlameMachine::blame(sender, recipient, msg, proof)`

which accept caller/peer-supplied `Participant` identifiers plus an attacker-serialized `EncryptedMessage` (`EncryptedMessage::read` is an explicitly in-scope untrusted input surface) and an `Option<EncryptionKeyProof>`. The `Participant` arguments in a deployed blame protocol are derived from accusation messages a peer sends; nothing in `blame` / `blame_internal` / `decrypt_with_proof` bounds-checks `sender`/`decryptor` against the registered participant set.

### Impact Explanation
An unprivileged participant in (or observer of) a PedPoP DKG session can submit a blame accusation naming a `sender` or `recipient` index outside the registered set — trivially, any index `> n`. Every honest node evaluating the accusation via `AdditionalBlameMachine::blame` or `BlameMachine::blame` panics mid-protocol. This is a remote denial of service of the key-generation/blame phase: the same crash triggers deterministically on all evaluators, aborting the DKG and any multisig instantiation dependent on it. Because `AdditionalBlameMachine::new` is explicitly designed for non-participants to evaluate blame on behalf of the set, the reachable surface includes third-party blame evaluators, widening the blast radius beyond direct counterparties.

### Likelihood Explanation
Triggering requires only sending a well-formed `EncryptedMessage` (or reusing a captured one — `blame` does not require the PoP to verify before reaching the panic for an unregistered `decryptor` when `proof` is `Some`, and the `commitments[&sender]` panic happens unconditionally after signature checks) together with an out-of-range participant index in the accusation. Index values are u16s attacker-controlled in the wire message; no valid key material or signature is needed for `sender`/`decryptor` indexes that were never registered. Exploitation is cheap and reliable wherever blame evaluation is wired to peer-submitted accusations.

### Recommendation
Replace panic-on-miss indexing with checked lookups that return `PedPoPError`:

- In `Decryption::decrypt_with_proof` (encryption.rs ~line 388): use `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` (or a new `UnknownParticipant` variant propagated up as `PedPoPError`).
- In `BlameMachine::blame_internal` (pedpop/src/lib.rs ~line 599): fetch `self.commitments.get(&sender)` and treat a miss as sender-blame or a dedicated error rather than panicking.
- Add an early bounds check at the top of `blame`/`blame_internal`: reject `u16::from(sender) > n` or `u16::from(recipient) > n` before touching any map.

### Proof of Concept
```rust
// Standalone; needs only the pedpop/dkg/ciphersuite crates, e.g. over Ristretto.
// Setup: run a 2-of-2 PedPoP DKG to completion so `commitment_msgs` exist, or
// construct AdditionalBlameMachine directly from the broadcast round-1 messages.

// n = 2 registered participants; attacker supplies index 3.
let sender = Participant::new(3).unwrap();      // never registered -> no commitments entry
let recipient = Participant::new(1).unwrap();
let msg: EncryptedMessage<C, SecretShare<C::F>> = /* any captured or crafted message */;

// This indexes self.commitments[&sender] inside blame_internal -> panic
additional_blame_machine.blame(sender, recipient, msg, None);

// Variant hitting encryption.rs: recipient index 3 has no enc_keys entry.
// Pass proof = Some(any EncryptionKeyProof<C>) so decrypt_with_proof reaches
// `self.enc_keys[&decryptor]` -> panic
blame_machine.blame(sender_valid, Participant::new(3).unwrap(), msg, Some(proof));
```

Both paths panic with `HashMap` "key not found" instead of returning `PedPoPError`, crashing the evaluator — the Serai analog of a slice/index-out-of-bounds DoS triggered by attacker-controlled inputs.