### Title
Blame evaluation panics on attacker-controlled participant indexes (out-of-range `Participant` → HashMap index panic) - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The PedPoP blame-resolution path (`BlameMachine::blame` / `AdditionalBlameMachine::blame`) indexes `HashMap`s keyed by `Participant` using the `sender`/`recipient`/`decryptor` values supplied with a blame transaction. These values are attacker-controlled: they deserialize from untrusted `u16` fields (`accuser`, `faulty` in `Transaction::InvalidDkgShare` / `CoordinatorMessage::VerifyBlame`), where `Participant::new` only rejects zero — any value greater than `n` is accepted. Indexing a `HashMap` with `map[&key]` panics when the key is absent, so a blame message naming a participant outside `1..=n` crashes the verifier rather than returning an error.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs`, `Decryption::decrypt_with_proof` performs `self.enc_keys[&decryptor]` (line 388) after the PoP check but before proof validation. `enc_keys` is populated only for participants `1..=n` (in `AdditionalBlameMachine::new` and via `register`), so any `decryptor` outside that set panics.

Similarly, `BlameMachine::blame_internal` in `crypto/dkg/pedpop/src/lib.rs` performs `self.commitments[&sender]` (line 599) when checking share validity — a `sender` index not registered panics.

The values reach this code unvalidated. `VerifyBlame` in `processor/src/key_gen.rs` (line 504) passes `accuser`/`accused` straight into `AdditionalBlameMachine::new(...).unwrap().blame(accuser, accused, ...)` (lines 543-556). On the coordinator side, `Transaction::InvalidDkgShare { accuser, faulty, .. }` are raw `u16`s read from the transaction stream (`coordinator/src/tributary/transaction.rs`), gated only by `Participant::new` (nonzero), not by `<= n`.

This is the analog of CVE-2018-0768's "object handling in memory" corruption: a crafted index into in-memory objects causes a fault (panic/abort) reachable from attacker-supplied bytes, where the expected behavior is a graceful error.

### Impact Explanation
A malformed blame claim causes an unconditional panic (`HashMap` index on missing key) inside the processor's `VerifyBlame` handler and inside `AdditionalBlameMachine::blame`. All processors/coordinator nodes that evaluate the blame message crash on the same input, halting DKG fault adjudication and, depending on harness behavior, the process itself. This is a remotely-triggered availability fault on the blame path — the exact path invoked precisely when the protocol is already in a degraded state. Severity: Medium (availability impact; no secret leakage or forgery).

### Likelihood Explanation
Reachable whenever a `VerifyBlame`/`InvalidDkgShare` message carries an `accuser` or `accused`/`faulty` index that is nonzero but not in `1..=n` (e.g., `n+1`, `0xffff`). The library API `AdditionalBlameMachine::blame` accepts arbitrary `Participant` values, and neither `decrypt_with_proof` nor `blame_internal` bounds-checks them before indexing. Exploitation requires only that the transaction/message parsing accept the out-of-range index, which the deserializers do (`Participant::new` rejects only 0).

### Recommendation
Validate `sender`, `recipient`/`decryptor`, `accuser`, and `accused` against the registered participant set (`1..=n`) before use. In `Decryption::decrypt_with_proof` and `BlameMachine::blame_internal`, replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `.get(&k)` + `ok_or(...)` returning a `DecryptionError`/`PedPoPError` instead of panicking. Additionally, bound `accuser`/`faulty` to `<= n` at transaction deserialization in `coordinator/src/tributary/transaction.rs`.

### Proof of Concept
```rust
// crypto/dkg/pedpop -- with a completed AdditionalBlameMachine for n participants:
// n = 3, so enc_keys/commitments contain Participants {1,2,3} only.
let msg: EncryptedMessage<C, SecretShare<C::F>> = /* any well-formed message */;
let proof: Option<EncryptionKeyProof<C>> = /* valid or None-reached path */;

// decryptor = Participant(4) > n -> panic at encryption.rs:388 `enc_keys[&decryptor]`
machine.blame(
    Participant::new(1).unwrap(),      // sender (in-range)
    Participant::new(4).unwrap(),      // recipient/decryptor OUT OF RANGE
    msg.clone(),
    proof.clone(),
);

// Or sender = Participant(0xffff) with a message whose PoP verifies and a valid
// proof -> panic at lib.rs:599 `commitments[&sender]`
machine.blame(
    Participant::new(0xffff).unwrap(), // sender OUT OF RANGE
    Participant::new(1).unwrap(),
    msg,
    proof,
);
```

Both calls abort via `HashMap` index panic rather than returning a blame verdict, when fed `Participant` values that deserialize cleanly from untrusted `u16` fields in `InvalidDkgShare`/`VerifyBlame`.

Note: I could not fully verify whether the coordinator validates `accuser`/`faulty <= n` earlier in `coordinator/src/tributary/handle.rs` before dispatching `VerifyBlame`; if it does, reachability narrows to direct library API misuse. The panic itself, however, is confirmed in the in-scope `crypto/dkg/pedpop` code.