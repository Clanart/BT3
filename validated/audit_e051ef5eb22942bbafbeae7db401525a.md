### Title
Unvalidated `Participant` indexes in PedPoP blame decryption cause a reachable panic (DoS) on attacker-supplied accusation input - (File: crypto/dkg/pedpop/src/lib.rs + crypto/dkg/pedpop/src/encryption.rs)

### Summary
The ChakraCore report describes memory corruption triggered by crafted input reaching an internal engine. The closest analog in Serai's in-scope code is an unguarded `HashMap` index on attacker-influenced `Participant` values inside the PedPoP blame/decryption path. `BlameMachine::blame`, `AdditionalBlameMachine::blame`, and the internal `blame_internal` / `Decryption::decrypt_with_proof` index `self.commitments[&sender]` and `self.enc_keys[&decryptor]` without ever checking that `sender`/`recipient` are registered participants, so an out-of-range index panics.

### Finding Description
`Participant` is a public tuple struct (`pub struct Participant(pub u16)` pattern; it is constructed directly as `Participant(l)` in `ThresholdKeys::read`), so a caller processing an accusation can end up with arbitrary `u16` values — including `0` and values `> n` — as `sender`/`recipient`.

In `blame_internal` (`crypto/dkg/pedpop/src/lib.rs:575-609`), `self.commitments[&sender]` is indexed directly at line 599 inside `share_verification_statements(...)`, panicking if `sender` was never a registered commitment key. Earlier, `Decryption::decrypt_with_proof` (`crypto/dkg/pedpop/src/encryption.rs:366-397`) indexes `self.enc_keys[&decryptor]` at line 388 while verifying the supplied `EncryptionKeyProof` — before any range check on `decryptor`. `enc_keys` only ever contains the participants registered via `register` (1..=n), so `recipient = Participant(n+1)` or `Participant(0)` panics unconditionally.

The `pop` signature check does not save this: the accuser/accused can supply an `EncryptedMessage` with a self-created ephemeral key and a valid Schnorr PoP (the code's own `encrypt` shows exactly how such a message is built and that the PoP binds `from`, `key`, `nonce`, `msg` — all attacker-chosen). `AdditionalBlameMachine::new` similarly populates `enc_keys` only for `1..=n`, so `AdditionalBlameMachine::blame` is equally reachable.

### Impact Explanation
A malicious DKG participant (or anyone able to submit an accusation/blame evaluation request to an honest validator or arbitrator node) can crash the process evaluating blame by naming a non-existent `sender` or `recipient`. This is a denial of service against the DKG fault-attribution mechanism: the honest party aborts instead of producing a blame verdict, allowing the actual faulty party to evade identification and forcing a protocol restart. In Rust this is the direct analog of the reported memory-corruption class: untrusted input reaches unchecked indexing in security-critical code.

### Likelihood Explanation
Reachability requires only that the attacker supply or influence the `sender`/`recipient`/`msg`/`proof` arguments to `blame()` — public API inputs, and the accusation flow explicitly takes "a copy of the encrypted secret share from the accused sender to the accusing recipient" from external sources. No collusion, leaked keys, or integrator misuse of a documented-MUST is required: nothing in the `blame` / `AdditionalBlameMachine::blame` signatures or docs states the indexes must be in-range (contrast `AdditionalBlameMachine::new`, which does document its validity assumption for the *commitment messages*, not for the blame arguments).

### Recommendation
In `blame_internal` and `Decryption::decrypt_with_proof`, replace `self.commitments[&sender]` / `self.enc_keys[&decryptor]` indexing with `.get()` and return an explicit fault verdict (e.g. blame the accuser or return an error) when the participant is unknown. `Participant` arguments should be validated against `1..=n` at the API boundary of `blame`, `AdditionalBlameMachine::blame`, and `decrypt_with_proof`.

### Proof of Concept
```rust
// After a PedPoP DKG completes, an honest node holds a BlameMachine or an
// AdditionalBlameMachine built over participants 1..=n.
// An accuser submits an accusation naming a non-existent recipient:

let sender = Participant::new(2).unwrap();           // a real participant
let bogus  = Participant(0);                          // or Participant(n + 1)

// `msg` is any EncryptedMessage with a valid PoP for `sender`, e.g. one the
// attacker generated themselves via a run of the protocol, and `proof` is
// Some(attacker_crafted EncryptionKeyProof).
let faulty = additional_blame_machine.blame(sender, bogus, msg, Some(proof));
// Panics inside decrypt_with_proof at `self.enc_keys[&decryptor]`
// (crypto/dkg/pedpop/src/encryption.rs:388) — HashMap index on missing key.
```

Same panic via `self.commitments[&sender]` at `crypto/dkg/pedpop/src/lib.rs:599` when `sender` is out of range and the proof path is bypassed (or reaches the share-verification step).