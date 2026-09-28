### Title
Missing participant-index validity check causes indexing panic in PedPoP blame path - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` without checking that the `decryptor` participant index is actually a registered key. The public `BlameMachine::blame` / `blame_internal` path (`crypto/dkg/pedpop/src/lib.rs:575-609`) forwards an attacker-influenced `recipient: Participant` directly as `decryptor`. Similarly, `blame_internal` later indexes `self.commitments[&sender]` with the unvalidated `sender`. A participant index that is out of range (`> n`) or equal to our own index `i` (which is never inserted into `enc_keys`, since `verify_r1` skips the missing own-commitment entry at `crypto/dkg/pedpop/src/lib.rs:313-315` and `encrypt` only iterates `l != i` at `crypto/dkg/pedpop/src/lib.rs:359-364`) causes a `HashMap` index panic, crashing the calling process.

### Finding Description
The bug class of CVE-2021-47174 — invoking a specialized path without checking that its precondition holds in the current context — maps onto Serai as unvalidated participant indexes used as map keys in the blame/decryption path:

- `crypto/dkg/pedpop/src/encryption.rs:388`: `self.enc_keys[&decryptor]` — panics if `decryptor` is not present. `enc_keys` is only populated by `Decryption::register` (`encryption.rs:351-362`) for participants who actually sent an `EncryptionKeyMessage` in round 1, and never for our own index `i`.
- `crypto/dkg/pedpop/src/lib.rs:599`: `self.commitments[&sender]` — same pattern with the `sender` index.
- `BlameMachine::blame` (`lib.rs:623-632`) is `pub` and performs no `validate_map`-style check that `sender`/`recipient` are within `1..=n` or correspond to registered entries, unlike `verify_r1` (`lib.rs:305-309`) and `calculate_share` (`lib.rs:468-472`) which do validate.

`Participant::new` accepts any non-zero `u16`, so indexes like `n+1` or `u16::MAX` are representable and reachable from deserialized/external data.

### Impact Explanation
A panic in a validator/coordinator process handling DKG blame messages is an availability failure: any party (or any code path relaying an accusation with arbitrary `sender`/`recipient` fields) can crash the machine by supplying a `recipient` equal to our own index `i` — the natural case when *we* are the party whose received share is disputed — or any participant index that was never registered (a participant who failed to send round-1 commitments, or simply an out-of-range index). This aborts the DKG/blame resolution via panic rather than a handled error, matching the CVE's impact profile (denial of availability from a reachable path lacking a context check).

### Likelihood Explanation
Likelihood is moderate. `blame` is reached whenever a disputed secret share must be adjudicated, and its `sender`/`recipient` arguments are protocol inputs not locally validated. The `recipient == i` case arises in ordinary (non-adversarial) usage if the local node's own accusation is processed through this path, and out-of-range indexes are trivially supplyable by an unprivileged counterparty's accusation payload. The only mitigating factor is that callers may pre-validate indexes outside this library.

### Recommendation
In `Decryption::decrypt_with_proof` and `BlameMachine::blame_internal`, replace indexing with `get` and return a `Result`/identify the faulty party on missing keys:

```rust
// encryption.rs
let Some(dec_key) = self.enc_keys.get(&decryptor) else {
  return Err(DecryptionError::InvalidProof);
};
```

and in `blame_internal`, fetch `self.commitments.get(&sender)` and treat a missing entry as `sender` fault (or add explicit `sender`/`recipient` bounds checks against `params.n()` at the top of `blame`).

### Proof of Concept
```rust
// After a KeyMachine completes calculate_share and returns BlameMachine,
// call blame with a recipient that is not a registered decryption key —
// e.g., our own index i, or any Participant > n:
let participant = Participant::new(u16::from(params.n()) + 1).unwrap();
// or participant = params.i();
let msg: EncryptedMessage<C, SecretShare<C::F>> = /* any well-formed EncryptedMessage */;
// Panics inside decrypt_with_proof at `self.enc_keys[&decryptor]`
let (_machine, _faulty) = blame_machine.blame(sender, participant, msg, None);
```

The panic occurs at `crypto/dkg/pedpop/src/encryption.rs:388` (`self.enc_keys[&decryptor]`) before the proof/signature logic can reject anything, because the map lookup itself dereferences a non-existent key.