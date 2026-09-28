### Title
Panic via out-of-range participant index in PedPoP blame evaluation causes node crash (DoS) - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` accept `sender` and `recipient` participant indexes and pass them to `blame_internal`, which indexes `self.commitments[&sender]` and `self.encryption`'s `enc_keys[&decryptor]` with the `HashMap` `Index` operator. Any participant index that was never inserted — trivially craftable by a remote party (e.g. `Participant(n + 1)`, or any index whose commitments were not registered) — causes a panic instead of a clean error, crashing the process evaluating a blame accusation.

### Finding Description
`blame_internal` decrypts the accused `EncryptedMessage` and verifies the share against `self.commitments[&sender]`:

- `crypto/dkg/pedpop/src/lib.rs` line 599: `&self.commitments[&sender]` — `HashMap`'s `Index` impl panics on a missing key.
- `crypto/dkg/pedpop/src/encryption.rs` line 388: `self.enc_keys[&decryptor]` inside `Decryption::decrypt_with_proof` — same panic when the `recipient`/decryptor index has no registered encryption key.
- `crypto/dkg/pedpop/src/encryption.rs` line 466: `self.decryption.enc_keys[&participant]` in `Encryption::encrypt` has the same shape, but is not attacker-driven; the blame path is the reachable one.

`enc_keys` is only populated for indexes `1 ..= n` (`Decryption::register` at `crypto/dkg/pedpop/src/encryption.rs:352-362`, populated in `verify_r1` at `crypto/dkg/pedpop/src/lib.rs:313-315` and in `AdditionalBlameMachine::new` at `lib.rs:656-659`), and `commitments` likewise only contains `1 ..= n`. `Participant` is a bare `u16` newtype (`Participant::new` only rejects 0), so an accusing party can name `sender` or `recipient` values in `1 ..= 65535` that are not DKG members. There is no bounds check against `params.n()` or `contains_key` anywhere on this path — the only documented requirement is that the *message* was authenticated from the sender, which does not constrain the `sender`/`recipient` arguments chosen by the accuser.

This mirrors the CVE-2012-4434 bug class (a remote, authenticated-or-not party crashing the server with attacker-controlled input): here, untrusted DKG/blame-protocol input reaches `HashMap` indexing that aborts the process rather than returning `PedPoPError`.

### Impact Explanation
Any participant in a PedPoP DKG (or any party able to submit a blame accusation for evaluation by `AdditionalBlameMachine`, which is explicitly designed for non-member evaluators) can crash the evaluating node by submitting an accusation referencing a non-existent participant. This is a denial of service against the validator/processor evaluating blame — e.g. during the DKG or during slash/fault adjudication — matching the "server crash" impact of the original advisory. Because the panic occurs inside the library rather than being returned as `PedPoPError`, callers cannot gracefully handle it without `catch_unwind`.

### Likelihood Explanation
The trigger requires only that an attacker can cause `blame()`/`AdditionalBlameMachine::blame()` to be called with attacker-influenced participant indexes — the normal mode of operation for a blame protocol, where the accuser names the accused. `AdditionalBlameMachine::new` explicitly supports third-party blame evaluation, so the accuser need not even be a DKG member. The crafted index requires no valid cryptography; any out-of-range `u16` suffices. The only mitigating factor is if the integrator independently validates indexes before calling `blame`, which the API neither enforces nor documents (the only documented precondition is message authentication).

### Recommendation
Replace `HashMap` `Index` usage with `get` + error on the blame/decrypt path:

- In `blame_internal` (`crypto/dkg/pedpop/src/lib.rs`): look up `self.commitments.get(&sender)` and return the accusing party (`recipient`) as faulty — or a new `PedPoPError`/`Participant` result — when the key is absent, before `decrypt_with_proof` and before `share_verification_statements`.
- In `Decryption::decrypt_with_proof` (`crypto/dkg/pedpop/src/encryption.rs:388`): use `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` instead of indexing.
- In `Encryption::encrypt` (`encryption.rs:466`): prefer `get` and return an error rather than panicking.
- Alternatively/additionally, validate `sender` and `recipient` against `params.n()` at the top of `BlameMachine::blame` and `AdditionalBlameMachine::blame` and return the accuser as faulty when out of range.

### Proof of Concept
```rust
// After an honest DKG completes for n = 4 (or after constructing
// AdditionalBlameMachine::new(context, 4, commitment_msgs)), an accuser
// submits an accusation naming sender = Participant(5) (or recipient = 5).

use dkg_pedpop::*;
use ciphersuite::Ciphersuite;

// `machine`: BlameMachine<C> or AdditionalBlameMachine<C>
// `msg`: any well-formed EncryptedMessage<C, SecretShare<C::F>>
//        (contents irrelevant — the panic precedes/short-circuits use of it
//         via enc_keys[&decryptor] or commitments[&sender])
// `proof`: Option<EncryptionKeyProof<C>>

let attacker_index = Participant::new(5).unwrap(); // valid type, not a member (n = 4)

// Path A: panic in encryption.rs:388 — enc_keys[&5] has no key.
// Path B: if decryptor is in-range and a proof is supplied that verifies,
//         panic in lib.rs:599 — commitments[&5] has no key.
machine.blame(attacker_index, Participant::new(1).unwrap(), msg, None);
// => thread panics: "no entry found for key" — process aborts under panic=abort
//    or unwinds, crashing the blame-evaluation task. No PedPoPError is returned.
```

Confidence caveat: `HashMap::index` panicking on missing keys is certain from the cited code; the reachability assumption is that the integrator forwards accuser-chosen `sender`/`recipient` indexes into `blame`, which is the protocol's intended use and unguarded by the library.