### Title
Missing bounds/validity check on participant indexes in `BlameMachine::blame_internal` / `Decryption::decrypt_with_proof` causes panic via unchecked map indexing - (File: crypto/dkg/src/../dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame` and `AdditionalBlameMachine::blame` accept `sender` and `recipient` `Participant` values derived from blame-accusation protocol messages and use them as direct indexes into fixed-size `HashMap`s (`self.commitments[&sender]` in `crypto/dkg/pedpop/src/lib.rs` line 599, and `self.enc_keys[&decryptor]` in `crypto/dkg/pedpop/src/encryption.rs` line 388) without first verifying that the participant is within `1..=n` and present in the map. This is the same bug class as CVE-2023-52674: a caller-supplied value used as an index without a clamp/bounds check, producing an out-of-bounds access (here, a `HashMap` indexing panic).

### Finding Description
- `BlameMachine::blame` / `AdditionalBlameMachine::blame` are public APIs that take `sender: Participant` and `recipient: Participant` supplied by the caller from unauthenticated-width protocol data. Neither validates `u16::from(participant) <= n` nor `map.contains_key(...)`.
- `blame_internal` calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` which, when a `proof` is present, evaluates `self.enc_keys[&decryptor]` (`crypto/dkg/pedpop/src/encryption.rs:388`). `enc_keys` only contains entries for participants `1..=n` registered via `Decryption::register` (`encryption.rs:351-362`), so any `recipient` index `> n` — or one absent because registration was skipped — panics.
- If `decrypt_with_proof` succeeds, `blame_internal` then evaluates `&self.commitments[&sender]` (`crypto/dkg/pedpop/src/lib.rs:599`). `commitments` is likewise keyed only by `all_participant_indexes()` (populated in `verify_r1`/`AdditionalBlameMachine::new`, `lib.rs:313-331`, `lib.rs:656-659`), so a `sender` index `> n` panics even when the signature and DLEq proof are valid for a real decryptor.
- Note the ordering subtlety: for the `enc_keys[&decryptor]` panic to fire, `msg.pop` must first verify (a valid `SchnorrSignature` under `msg.key` over `pop_challenge`), which an attacker who is a legitimate participant can produce for their own key; alternatively the accusation can name `recipient` as any index `> n` while supplying `proof` — the `pop.verify` still runs before the index, but the panic occurs as soon as a `Some(proof)` branch reaches `enc_keys[&decryptor]`.

### Impact Explanation
A panic in `blame`/`blame_internal` aborts the calling task/thread. In the key-generation pipeline (`processor/src/key_gen.rs` consumes these machines and maps results to `ProcessorMessage::InvalidShare`/blame flows), an attacker-influenced `Participant` value embedded in a blame accusation can crash the honest node processing the accusation, denying completion of the DKG and preventing the multisig from ever producing keys/signatures — a liveness/availability failure analogous to the array-OOB denial in the reference advisory.

### Likelihood Explanation
`Participant` is a `u16` with only a non-zero check (`crypto/dkg/src/lib.rs:29-35`); nothing bounds it to `n` at the `blame` boundary. Any value `n < sender/recipient <= u16::MAX` triggers the panic. Reachability requires the caller to route an accusation containing an out-of-range participant index into `blame` — plausible since the library explicitly delegates authentication/validation of accusations to the caller (`lib.rs:616-622`), and the machine intentionally accepts arbitrary accusations to "determine the faulty party".

### Recommendation
- In `blame_internal` and `decrypt_with_proof`, replace `map[&key]` with `map.get(&key)` and return an error/`Participant` verdict (e.g., blame the accuser or reject the accusation) when `sender`/`recipient` is not a registered participant.
- Additionally validate `u16::from(sender) <= params.n()` / `u16::from(recipient) <= params.n()` at the `blame`/`AdditionalBlameMachine::blame` entry points, mirroring the `ThresholdParams::new` `InvalidParticipant` check (`crypto/dkg/src/lib.rs:174-176`).

### Proof of Concept
```rust
// Construct a normal PedPoP key generation among n=3, t=3 and obtain a
// BlameMachine (or AdditionalBlameMachine::new(context, 3, commitment_msgs)).
// Then invoke blame with an out-of-range participant index:

let sender = Participant::new(0xFFFF).unwrap();   // > n = 3, valid non-zero Participant
let recipient = Participant::new(1).unwrap();

// Path A: panics at crypto/dkg/pedpop/src/lib.rs:599 on `self.commitments[&sender]`
// if decrypt_with_proof returns Ok.
// Path B: panics at crypto/dkg/pedpop/src/encryption.rs:388 on
// `self.enc_keys[&decryptor]` when `recipient` is out of range and proof.is_some().

let (_machine, _faulty) = blame_machine.blame(sender, recipient, msg, Some(proof));
// thread panics: "no entry found for key" — the participant index was never
// clamped/checked against n before use as a map index.
```

Confidence note: the panic root cause is directly confirmed at `crypto/dkg/pedpop/src/lib.rs:599` and `crypto/dkg/pedpop/src/encryption.rs:388`; whether an unprivileged external party can inject an out-of-range `Participant` depends on the caller's parsing of blame accusations (outside the in-scope crypto crates), which the library's own documentation delegates to the integrator — so the reachable reachability is through any caller passing accusation-derived indexes, consistent with the library's intended usage.