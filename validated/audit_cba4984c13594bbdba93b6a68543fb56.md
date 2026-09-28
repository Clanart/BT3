### Title
Attacker-controlled `Participant` index causes panic (abort) in PedPoP blame handling via unchecked `HashMap`/`Vec` indexing - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The PJSIP CVE is a remote out-of-bounds access where a value taken from the wire (payload type) indexes a fixed-size table without bounds validation. Serai is Rust, so the memory-corruption half does not translate, but the identical root cause exists: `Participant` values that arrive over the wire (only validated to be non-zero by `Participant::new`, crypto/dkg/src/lib.rs:29-35) are used to index internal collections (`self.commitments[&sender]` at crypto/dkg/pedpop/src/lib.rs:599 and `self.enc_keys[&decryptor]` at crypto/dkg/pedpop/src/encryption.rs:388) without checking `participant <= n`. An out-of-range participant index in a blame accusation panics, aborting blame evaluation / the DKG — a remote-triggered denial of service, the same practical impact as the advisory.

### Finding Description
`BlameMachine::blame` / `AdditionalBlameMachine::blame` take `sender` and `recipient` as `Participant` arguments describing an accusation received from the network, and forward them to `blame_internal` (crypto/dkg/pedpop/src/lib.rs:575-609). `blame_internal` does `self.commitments[&sender]` (line 599), which panics if `sender` was not in the DKG set (`> n`). Earlier, `Decryption::decrypt_with_proof` does `self.enc_keys[&decryptor]` (crypto/dkg/pedpop/src/encryption.rs:388), panicking for `decryptor > n`. Neither caller nor callee bounds-checks the index against `params.n()` before indexing.

`Participant` is deserialized from arbitrary `u16` values — its only invariant is `!= 0` (crypto/dkg/src/lib.rs:29-35, 121-126) — so any `u16` in `1..=u16::MAX` is accepted, while the maps only contain keys `1..=n`.

`AdditionalBlameMachine::new` (crypto/dkg/pedpop/src/lib.rs:649-662) exists specifically to let a third party evaluate blame accusations originating from other participants, i.e., `sender`/`recipient` are remote-controlled. It also fails to re-validate `msg.commitments.len() == t` after `encryption.register` (unlike `verify_r1`, crypto/dkg/pedpop/src/lib.rs:317-319), so a malformed `Commitments` message is stored into the map used by `share_verification_statements` — the commitment `Vec` is then indexed element-wise in `exponential` (crypto/dkg/pedpop/src/lib.rs:423-426) using its own (attacker-chosen) length, silently producing a wrong verification statement rather than rejecting the malformed message.

### Impact Explanation
Any participant (or, for `AdditionalBlameMachine`, any source of blame accusations) can submit an accusation naming `sender` or `recipient` outside `1..=n`, e.g. `Participant::new(n + 1)`. The indexing panics (`HashMap` index operator on a missing key), unwinding the blame-handling code path. Since PedPoP mandates abort on fault, and blame evaluation is the mechanism for deciding who is at fault, crashing the blame evaluator prevents the DKG from resolving faults — a remote denial of service reachable purely with attacker-chosen `Participant` values in a message the victim is expected to process. This mirrors the CVE: attacker-supplied index → access outside the valid table range → crash.

### Likelihood Explanation
Blame accusations are, by design, unauthenticated-with-respect-to-content peer inputs — a faulty/malicious participant is precisely who this code handles. No threshold, collusion, leaked key, or trusted-input assumption is required; only a DKG participant submitting a blame claim with an out-of-range participant index. `AdditionalBlameMachine::new` even documents that callers may pass accusations from arbitrary parties. The panic is deterministic — no race or probabilistic condition.

### Recommendation
In `BlameMachine::blame`, `AdditionalBlameMachine::blame`, `blame_internal`, and `Decryption::decrypt_with_proof`, validate `sender` and `recipient` against the key set (or `params.n()`) and return a defined error/`Participant` result instead of indexing with `[]`; use `HashMap::get` with an explicit fault outcome. Additionally, `AdditionalBlameMachine::new` should enforce `msg.commitments.len() == t` (as `verify_r1` does) before storing commitments, so malformed commitment vectors cannot propagate into `share_verification_statements`.

### Proof of Concept
```rust
// context: a completed PedPoP DKG among n participants, or a third-party
// AdditionalBlameMachine built from the n commitment messages.
// Attacker (a participant or relayer of blame accusations) supplies:

let evil_sender = Participant::new(n + 1).unwrap(); // valid per Participant::new (non-zero)

// Any EncryptedMessage<SecretShare> bytes; contents never matter.
let msg: EncryptedMessage<C, SecretShare<C::F>> = ...;

// BlameMachine::blame -> blame_internal -> self.commitments[&evil_sender]
// HashMap index on a missing key -> panic, aborting blame evaluation.
machine.blame(evil_sender, recipient, msg, proof);

// Equivalently, with sender in-range but recipient out-of-range:
// decrypt_with_proof -> self.enc_keys[&decryptor] -> panic.
machine.blame(sender, Participant::new(u16::MAX).unwrap(), msg, Some(proof));
```

The first panic occurs at `self.commitments[&sender]` (crypto/dkg/pedpop/src/lib.rs:599); the second at `self.enc_keys[&decryptor]` (crypto/dkg/pedpop/src/encryption.rs:388). Both are reachable with no special privileges — the panic replaces the intended "return which party is faulty" result with a crash, achieving remote denial of service identical in class and impact to CVE-2026-57159.