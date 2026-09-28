### Title
Panic on unregistered participant index in blame resolution causes node crash - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to CVE-2021-20272 (an assertion/crash reachable by a crafted request), `BlameMachine::blame` / `AdditionalBlameMachine::blame` panic when handed a `sender` or `recipient` `Participant` that was not registered in the DKG. Both `Decryption::decrypt_with_proof` and `BlameMachine::blame_internal` index `HashMap`s with untrusted `Participant` values (`self.enc_keys[&decryptor]`, `self.commitments[&sender]`), which panics on missing keys.

### Finding Description
`Participant` is validated only as a non-zero `u16`; it is not range-checked against the DKG's `n` in the blame path. In `decrypt_with_proof`, when a blame proof is present, `self.enc_keys[&decryptor]` is evaluated at crypto/dkg/pedpop/src/encryption.rs:388 using HashMap's `Index` impl, which panics if `decryptor` was never registered. Similarly, `blame_internal` reads `self.commitments[&sender]` at crypto/dkg/pedpop/src/lib.rs:599, which panics if `sender` is not in the commitment map (e.g., `sender` > `n`, or for `AdditionalBlameMachine`, any index not supplied at construction). `blame()` is a public API meant to process accusations originating from *other* participants; the `sender`/`recipient` arguments and the `msg`/`proof` bytes are attacker-influenced protocol data. A malicious (or merely buggy) participant broadcasting a blame accusation naming an out-of-range participant index — or a recipient whose encryption key was never registered — triggers an unwinding panic in any node that evaluates the accusation.

Contrast with the signing path, which does validate: `AlgorithmSignMachine::sign` rejects `included` indexes greater than `n` and duplicates before use (crypto/frost/src/sign.rs:302-310), and `validate_map` enforces membership in `pedpop` round handling. No equivalent check exists on `blame()`.

### Impact Explanation
An unauthenticated protocol participant can crash any validator/node that evaluates a blame accusation containing a participant index outside the registered set. This is a remote, input-triggered denial of service — the same availability impact class as the reference CVE (assertion failure via crafted request → server crash). In a threshold-signing deployment, crashing nodes during the blame/aborted-DKG path can also stall key generation or signing recovery.

### Likelihood Explanation
Blame accusations are routine protocol traffic whenever a share fails verification, and the accused/accuser fields and message bytes come from peers. Sending an accusation with `sender` or `recipient` set to an arbitrary non-zero `u16` (e.g., `n + 1`, or a participant who never registered an encryption key) is trivially within reach of any participant. No collusion or key material is needed; a single crafted message panics the handler.

### Recommendation
Replace `HashMap` indexing with fallible lookups in `blame_internal`/`decrypt_with_proof` (e.g., `get()` returning a `PedPoPError`), and validate that `sender`/`recipient` are within `1..=n` (and registered) before processing. Alternatively, perform the blame `multiexp_vartime` check before the encryption-key lookup so an invalid sender is rejected via the `DecryptionError`/`InvalidShare` path rather than indexing `enc_keys`.

### Proof of Concept
After a DKG completes (or using `AdditionalBlameMachine::new` with valid commitments for participants `1..=n`), call:

```rust
// recipient = Participant::new(n + 1) — never registered in Decryption.enc_keys
blame_machine.blame(
    sender,                       // valid registered participant
    Participant::new(n + 1).unwrap(),
    msg,                          // any EncryptedMessage with a valid PoP for `sender`
    Some(proof),                  // any EncryptionKeyProof
);
```

`decrypt_with_proof` reaches `self.enc_keys[&decryptor]` (encryption.rs:388) and panics with "key not found". Likewise, `AdditionalBlameMachine::blame` with `sender` set to an index not present in `commitment_msgs` at construction panics at `self.commitments[&sender]` (lib.rs:599).