### Title
Panic via unvalidated participant index in blame evaluation crashes the DKG - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary

The bug class in ALPINE-CVE-2018-10115 is a decoder accepting crafted input that drives the program into an incorrectly initialized/invalid state, producing a crash. The Serai analog lives in `blame_internal` at `crypto/dkg/pedpop/src/lib.rs`: the `sender` and `recipient` `Participant` values come from the caller (in production, from an authenticated-but-untrusted blame/share message), and `sender` is used to directly index `self.commitments[&sender]` (and inside `decrypt_with_proof`, per-sender encryption state). No check ensures `sender` or `recipient` is within `1..=n` or present in the commitments map.

### Finding Description

`BlameMachine::blame` and `AdditionalBlameMachine::blame` are public APIs reachable by feeding untrusted bytes to `EncryptedMessage::read` plus attacker-chosen `sender`/`recipient` participant indexes:

```rust
// crypto/dkg/pedpop/src/lib.rs
let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) { ... };
// ...
multiexp_vartime(&share_verification_statements::<C>(
  recipient,
  &self.commitments[&sender],   // <-- HashMap index panics if sender ∉ {1..=n}
  Zeroizing::new(share),
))
```

`self.commitments` is populated only for `1..=n` (in `KeyMachine` via `all_participant_indexes()`, in `AdditionalBlameMachine::new` via `1 ..= n`). A `Participant` with value `n + 1` (or any absent index — e.g., the accuser's own index, or an index whose commitment message was missing) causes `HashMap`'s `Index` impl to panic, unwinding/aborting the process handling the blame message. The same unchecked indexing pattern was previously seen as reachable in the blame-verification flow (`VerifyBlame` style handlers read `EncryptedMessage`s then invoke blame with attacker-supplied `accused` indexes).

This mirrors the CVE shape: a decoder (`EncryptedMessage::read` + blame dispatch) accepts crafted input that reaches an unguarded state lookup, converting malformed input into a fatal crash rather than a clean error.

### Impact Explanation

A single crafted blame message (or a share message evaluated under a forged sender index) crashes the validator evaluating blame. Since blame evaluation gates slashing/fault attribution in the DKG, a malicious participant can abort honest nodes' blame processing at will — a remote, unprivileged denial of service during a critical recovery path. This is an availability loss reachable purely from public inputs, with no collusion or leaked keys required.

### Likelihood Explanation

Any party able to submit a blame/share message (or induce one to be evaluated) controls `sender`/`accused`. Supplying an out-of-range index is trivial and deterministic; the panic is guaranteed because `HashMap::index` panics on absent keys.

### Recommendation

Validate `sender`/`recipient` in `blame_internal` (and `AdditionalBlameMachine::blame`) against `1..=n` / map membership before indexing, returning the accuser/`recipient` as faulty on invalid indexes; replace `self.commitments[&sender]` with `get(&sender)` plus an explicit fault result. Mirror the same check inside `decrypt_with_proof` for per-sender encryption state lookups.

### Proof of Concept

1. Complete or partially run a PedPoP DKG with `n` participants so an honest node holds a `BlameMachine`/`AdditionalBlameMachine` (commitments populated for `1..=n` only).
2. Submit an `EncryptedMessage` blame request with `sender = Participant::new(n + 1)` (or any index absent from the commitments map).
3. `blame_internal` reaches `self.commitments[&sender]` → `HashMap` index panic → the evaluator crashes instead of attributing fault.