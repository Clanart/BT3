### Title
Malicious preprocess with out-of-range participant index crashes signer via `unreachable!` on `FrostError::InvalidParticipant` - (File: crypto/frost/src/sign.rs)

### Summary
CVE-2016-9624 is a remote denial-of-service where a crafted input crashes the parser. The Serai analog lives in the FROST signing path: `AlgorithmSignMachine::sign` correctly rejects attacker-supplied preprocesses whose `Participant` index exceeds `n` by returning `FrostError::InvalidParticipant`, but every production caller maps that error to `unreachable!()`, turning a peer-controlled invalid input into a process abort.

### Finding Description
`AlgorithmSignMachine::sign` collects `included` from the keys of the caller-supplied `preprocesses` map plus our own index. At crypto/frost/src/sign.rs:302-304 it checks `u16::from(included.last()) > params.n()` and returns `FrostError::InvalidParticipant`. `Participant` indexes are deserialized as arbitrary `u16` values from peer/coordinator messages (`Participant::new(read_u16()?)` in crypto/dkg/src/lib.rs:600 accepts any nonzero u16, far beyond `n`).

All in-scope callers then treat `InvalidParticipant` as impossible:

- `coordinator/src/tributary/signing_protocol.rs:170-178` — `share_internal` matches `FrostError::InvalidParticipant(..) | InvalidSigningSet | InvalidParticipantQuantity | DuplicatedParticipant | MissingParticipant => unreachable!("{e:?}")`, panicking on attacker-influenced input.
- `processor/src/signer.rs:533-539`, `processor/src/batch_signer.rs:274-280`, `processor/src/cosigner.rs:187-193` — identical `unreachable!()` arms after `read_preprocess`/`sign` on `serialized_preprocesses`/`preprocesses` parsed from `CoordinatorMessage` payloads sent by other validators.

Note the preprocess bytes themselves are bounds-safe (`Commitments::read` reads exactly `algorithm.nonces()` elements, crypto/frost/src/nonce.rs:133-139), so the crash vector is the *key* of the preprocess map, not its contents. An attacker does not need a valid key share — they only need the coordinator to relay a preprocess list containing an entry keyed `Participant(n + 1)` (or any value > `n`); `read_preprocess` succeeds on well-formed bytes, and `sign` then hits `unreachable!`.

### Impact Explanation
A single malformed preprocess message aborts the signing handler on every honest node that receives it. In `signing_protocol.rs`/`signer.rs`/`batch_signer.rs`/`cosigner.rs` the panic unwinds (or aborts) the processor/coordinator task handling the signing session, denying service for that attempt — and, since the panic is deterministic per input, repeatedly for every retry carrying the same payload. This matches the CVE's "remote attacker causes crash via crafted input" shape; impact is availability-only (no key/share leakage), consistent with Medium severity.

### Likelihood Explanation
Any participant able to submit a preprocess (or get the coordinator to relay `SubstratePreprocesses`/equivalent with a bogus `Participant` key) triggers it deterministically — no race, no collusion, and no valid threshold key required beyond protocol access. The only mitigation would be upstream filtering of preprocess-map keys before `sign` is invoked; nothing in `share_internal`, `batch_signer`, or `cosigner` range-checks `Participant` values before handing the map to `machine.sign`.

### Recommendation
Do not map attacker-controllable `FrostError` variants to `unreachable!()`. In `coordinator/src/tributary/signing_protocol.rs` `share_internal`, `processor/src/signer.rs`, `processor/src/batch_signer.rs`, and `processor/src/cosigner.rs`, handle `InvalidParticipant`, `InvalidSigningSet`, `InvalidParticipantQuantity`, `DuplicatedParticipant`, and `MissingParticipant` the same way as `InvalidPreprocess`/`InvalidShare`: return an `InvalidParticipant`/`InvalidSigningSet` protocol message identifying the offender (or drop the session) instead of panicking. Additionally, pre-validate that every key in the incoming preprocess map satisfies `l <= n` before calling `sign`.

### Proof of Concept
```rust
// Attacker-controlled path (as exercised by e.g. share_internal / batch_signer):
// 1. Deserialize a preprocess message map containing an entry keyed
//    Participant::new(params.n() + 1).unwrap() with any well-formed commitment
//    bytes (Commitments::read only needs algorithm.nonces() group elements).
// 2. Honest node parses it fine, builds `included` = [i, ..., n + 1] and calls:
let res = machine.sign(preprocesses, msg);
// 3. crypto/frost/src/sign.rs:302-304 returns:
//    Err(FrostError::InvalidParticipant(n, Participant(n + 1)))
// 4. Caller's match arm `FrostError::InvalidParticipant(..) => unreachable!()`
//    (signing_protocol.rs:172-176) panics -> process abort.
```
Minimal Rust: with `ThresholdKeys` for `t=2, n=3` and an `AlgorithmSignMachine`, insert into the `preprocesses` HashMap a valid `Preprocess` under `Participant::new(4).unwrap()`; `sign` returns `InvalidParticipant`, and production callers panic at the `unreachable!` arm. Deterministic, single-message, remote-reachable crash.