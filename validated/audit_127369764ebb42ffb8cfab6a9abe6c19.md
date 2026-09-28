### Title
Out-of-range `Participant` in PedPoP blame evaluation panics via unchecked `HashMap` indexing - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2017-11555 describes an illegal address access reachable by crafted input causing a remote denial of service. The Serai analog is an unchecked indexing panic in the PedPoP DKG blame path: `Decryption::decrypt_with_proof` and `BlameMachine::blame_internal` index `HashMap`s with attacker-influenced `Participant` values using `map[&key]`, which panics when the participant index is not in the registered set (`1..=n`). Because `Participant` accepts any non-zero `u16` and neither `blame` API validates that `sender`/`recipient` are in range, a crafted blame accusation crashes the evaluating party.

### Finding Description
`Participant` is an unrestricted non-zero `u16` wrapper (`crypto/dkg/src/lib.rs:24-42`), so indexes up to 65535 can be supplied regardless of the DKG size `n`.

In `crypto/dkg/pedpop/src/encryption.rs`, `Decryption::register` only inserts encryption keys for participants actually present in the commitments round (`1..=n`), via `AdditionalBlameMachine::new` (`lib.rs:656-660`) or `Encryption::register`. Later, `decrypt_with_proof` performs:

```rust
// encryption.rs:388
&[self.enc_keys[&decryptor], *proof.key],
```

`self.enc_keys[&decryptor]` panics if `decryptor > n` or was never registered.

Similarly, in `crypto/dkg/pedpop/src/lib.rs`, `blame_internal` does:

```rust
// lib.rs:599
&self.commitments[&sender],
```

which panics if `sender` is a valid non-zero `Participant` not in `1..=n` (e.g., `n+1`).

The public entry points `BlameMachine::blame` (`lib.rs:623-632`), `AdditionalBlameMachine::blame` (`lib.rs:674-682`), and `BlameMachine::complete`-adjacent blame flow all route through `blame_internal` → `decrypt_with_proof` without any bound check on `sender`/`recipient`. The accompanying `msg: EncryptedMessage<C, SecretShare<C::F>>` and `proof: Option<EncryptionKeyProof<C>>` are fully attacker-controlled bytes parsed by `EncryptedMessage::read` (`encryption.rs:171-177`) and `EncryptionKeyProof::read` (`encryption.rs:267-269`). Crucially, the panic on `enc_keys[&decryptor]` occurs **before** any cryptographic check that could reject the accusation — the PoP signature verification at `encryption.rs:374` binds only to `from`, and a malicious sender can generate a valid PoP for any claimed `sender` index since they produce `key`, `pop`, and `msg` themselves.

### Impact Explanation
An unprivileged participant (or anyone able to submit a blame statement / accused share message to a node evaluating blame, e.g. via `ProcessorMessage::InvalidShare` handling) can deterministically panic the DKG/blame evaluation logic. In a validator/node context where panics abort the process or the async task driving key generation, this is a remote denial of service of the threshold-signing pipeline — mirroring the CVE's illegal-address-access → DoS class. `BlameMachine` documentation notes "Usage of invalid commitments is considered undefined behavior, and may cause everything from inaccurate blame to panics" (`lib.rs:646-648`), but `AdditionalBlameMachine::new` itself fully validates the commitments it registers; the missing validation is on the per-call `sender`/`recipient` arguments, which are attacker-influenced and not covered by that caveat.

### Likelihood Explanation
Medium. Exploitation requires reaching the blame path: an accuser must convince a node to evaluate a blame statement where either the accused `sender` or the accused `recipient` index lies outside `1..=n`. Participant indexes are not constrained by the type system to the session's `n`, so a malicious validator in the key-generation set (or any party whose accusations are relayed) can name `Participant::new(n+1).unwrap()` as recipient, or send a properly-signed `EncryptedMessage` under a bogus `sender` index, both yielding `enc_keys[&decryptor]` / `commitments[&sender]` panic before any validity check rejects the claim. No secret leakage is needed; a single crafted message suffices.

### Recommendation
Validate `sender` and `recipient` against the registered set before indexing:

- In `decrypt_with_proof`, replace `self.enc_keys[&decryptor]` with `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)` (or a dedicated error).
- In `blame_internal`, check `self.commitments.contains_key(&sender)` early and return `sender` as faulty (or a dedicated error) when absent.
- Alternatively, enforce at the `blame`/`AdditionalBlameMachine::blame` boundary that both participants are `<= n`.

### Proof of Concept
```rust
// n = 3 participant DKG completes; evaluator builds an AdditionalBlameMachine
let machine = AdditionalBlameMachine::<Ristretto>::new(context, 3, commitment_msgs).unwrap();

// Attacker submits a blame statement naming a recipient that was never registered.
// Participant(4) is a perfectly valid non-zero u16; nothing checks it against n.
let bogus_recipient = Participant::new(4).unwrap();

// msg is an EncryptedMessage whose PoP the attacker signs themselves for `sender`
// (PoP verification binds `from` = sender, which the attacker controls).
// decrypt_with_proof reaches self.enc_keys[&bogus_recipient] -> panic.
machine.blame(sender, bogus_recipient, msg, Some(proof));
```
PoP verification (`encryption.rs:374`) passes because the attacker generated `key`/`pop`/`msg` consistently for their claimed `from`; execution then reaches `encryption.rs:388` (`self.enc_keys[&decryptor]`) and panics. Symmetrically, passing `sender = Participant(4)` with a valid PoP reaches `lib.rs:599` (`self.commitments[&sender]`) and panics.

Note: if Serai's deployment only ever calls `blame` with the local participant as `recipient` and a previously-validated `sender`, reachability narrows to `sender`-side abuse; the `commitments[&sender]` panic remains reachable because the attacker fully controls the `sender` value bound into the PoP they sign.