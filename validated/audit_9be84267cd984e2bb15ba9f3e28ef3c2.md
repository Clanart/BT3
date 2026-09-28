### Title
Unvalidated participant indexes in PedPoP blame handling cause a panic (DoS) - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
CVE-2016-2168 is a null-pointer dereference reachable by a remote authenticated party via a crafted request header, crashing the server during an authorization check. The analog in Serai is an unchecked `HashMap` index in the PedPoP DKG blame path: `BlameMachine::blame_internal` / `AdditionalBlameMachine::blame` accept attacker-supplied `sender`/`recipient` participant IDs and index `self.commitments[&sender]` and `self.enc_keys[&decryptor]` without ever checking those IDs against the registered participant set, so any index outside `1..=n` panics the calling process.

### Finding Description
`AdditionalBlameMachine::new` populates `commitments` (and `encryption.enc_keys` via `Decryption::register`) only for `Participant::new(1) ..= Participant::new(n)` (pedpop/src/lib.rs:654-661). `blame()` then forwards caller-chosen `sender` and `recipient` into `blame_internal` (lib.rs:630, 681), which:

1. calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` — and inside `decrypt_with_proof`, `self.enc_keys[&decryptor]` (encryption.rs:388) is evaluated as part of the DLEq generators, panicking if `decryptor` was never registered;
2. on a successful decrypt, evaluates `&self.commitments[&sender]` (lib.rs:599) inside `share_verification_statements`, panicking if `sender` is not a key in the map.

Neither function validates `sender`/`recipient` against `1..=n` or against map membership. `Participant` is any non-zero `u16` (dkg/src/lib.rs:29-35), so a party only needs to supply e.g. `accused = Participant(n+1)` to hit the panic. This is reachable in practice through the blame-verification flow, where `accuser` and `accused` are taken directly from an incoming `VerifyBlame` message and passed into `AdditionalBlameMachine::new(...).blame(accuser, accused, ...)` (processor/src/key_gen.rs:504-549).

### Impact Explanation
A single crafted participant index in a blame/accusation message deterministically panics the process evaluating the blame (HashMap indexing on a missing key → `panic!`). This halts the validator/processor evaluating the blame proof, a remote, low-cost denial of service analogous to the Subversion `req_check_access` NULL dereference: a crafted, unauthenticated-in-content value short-circuits a check path and crashes the host.

### Likelihood Explanation
Reachability requires the caller to route blame messages to this API, which is exactly what the blame-verification flow does. The attacker needs the `msg`/`proof` path to reach the indexing line: for `decrypt_with_proof`, supplying any `proof: Some(_)` with an out-of-range `decryptor` panics at `enc_keys[&decryptor]`; for `commitments[&sender]`, the message's PoP must verify (the sender-signed `pop` over `msg`), which a legitimate DKG participant can satisfy for their own messages — or the attacker simply targets the `decryptor` index, which requires no valid message at all beyond parsing. No cryptographic break is needed; it is a pure crash primitive.

### Recommendation
Validate `sender` and `recipient` at the top of `blame_internal` (or in `AdditionalBlameMachine::blame`/`BlameMachine::blame`): reject indexes not present in `commitments`/`enc_keys` (i.e., not in `1..=n`) with a `PedPoPError` instead of indexing. Replace `self.commitments[&sender]` and `self.enc_keys[&decryptor]` with `.get(..)` plus an explicit error/early-return naming the faulty party.

### Proof of Concept
```rust
// Build a blame machine for n = 3 participants over Ristretto
let context = [0u8; 32];
let params = ThresholdParams::new(2, 3, Participant::new(1).unwrap()).unwrap();

// ... run KeyGenMachine to completion so `commitment_msgs` for 1..=3 exist ...
let machine = AdditionalBlameMachine::<Ristretto>::new(context, 3, commitment_msgs).unwrap();

// `accused`/`decryptor` = 4 is a valid non-zero Participant but was never registered.
let attacker = Participant::new(4).unwrap();
// Panic 1: enc_keys[&4] inside decrypt_with_proof (proof path)
machine.blame(attacker, attacker, any_encrypted_msg, Some(any_proof));
// or, if a verifying `msg` from a real sender is available:
// Panic 2: commitments[&4] inside blame_internal's share_verification_statements
```