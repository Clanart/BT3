### Title
Empty aggregate signature accepted — `SchnorrAggregator::complete` refuses to produce an empty aggregate, but `SchnorrAggregate::read`/`verify` accept the zero-signer encoding — ([File: crypto/schnorr/src/aggregate.rs])

### Summary
The analog of CVE-2025-32359 (a security invariant enforced only on the "client-side"/producer path but not on the API a remote party actually reaches) is the empty-aggregate check in `crypto/schnorr/src/aggregate.rs`. The producing side, `SchnorrAggregator::complete`, explicitly rejects the empty case (`if self.sigs.is_empty() { return None; }`, lines 175–178) — establishing that "an aggregate must contain at least one signature" is an intended security invariant. However, the deserialized path an unprivileged party controls — `SchnorrAggregate::read` (lines 77–88) followed by `SchnorrAggregate::verify` (lines 127–146) — never re-enforces it. A length-0 `Rs` vector paired with `s = 0` deserializes cleanly and verifies successfully against an empty `keys_and_challenges` slice.

### Finding Description
- `SchnorrAggregator::complete` returns `None` when no signatures were aggregated, encoding the invariant that a valid `SchnorrAggregate` must be non-empty (aggregate.rs:175-178).
- `SchnorrAggregate::read` reads a `u32` count and then that many points; `u32::from_le_bytes(len) == 0` is accepted, followed by any canonical `s` via `C::read_F` (aggregate.rs:77-87). `read_F` permits the zero scalar (`crypto/ciphersuite/src/lib.rs:74-83`).
- `SchnorrAggregate::verify` only checks `self.Rs.len() != keys_and_challenges.len()`, then evaluates `multiexp_vartime(&pairs).is_identity()`. With zero signers, `pairs` consists solely of `(-self.s, C::generator())`; with `s = 0` this sum is the identity, so `verify` returns `true` (aggregate.rs:127-146).
- The reachable attack surface exists in-tree: `Validators::verify_aggregate` in `coordinator/tributary/src/tendermint/mod.rs:201-229` calls `SchnorrAggregate::read` on attacker-supplied bytes and then `aggregate.verify(DST, &[])` whenever `signers` is empty — `signers.len() != aggregate.Rs().len()` (line 211) passes for the empty encoding, and every signer challenge loop is vacuous.

So the producer-side guard is the "front-end enforcement," and the read/verify path is the "direct API" that skips it — exactly the bug class of the CVE.

### Impact Explanation
An unprivileged party can forge an aggregate signature — the 4-byte length `0x00000000` followed by 32 zero bytes — that `SchnorrAggregate::read` accepts and `SchnorrAggregate::verify` returns `true` for, whenever the verifier's signer set is empty. In the Tendermint `SignatureScheme` integration, this means an attacker-supplied `AggregateSignature` authenticates an empty validator set, i.e., a "signature" produced by nobody is treated as cryptographically valid. Any downstream logic that gates on `verify_aggregate` returning true (e.g., accepting commit data or evidence tied to an empty/lookup-failed validator set) is authenticating on the basis of a forged signature.

### Likelihood Explanation
The trigger requires a verifier path that can be driven to an empty `keys_and_challenges`/`signers` set. In `verify_aggregate` this happens whenever the caller supplies an empty `signers` slice — for example, when validator-set lookup yields no members for the referenced context — combined with attacker-controlled signature bytes. It is reachable purely from public input bytes fed to `SchnorrAggregate::read`, requires no key material, no collusion, and no malformed encodings (the forgery is fully canonical).

### Recommendation
Enforce the non-emptiness invariant on the verification/deserialization boundary, matching `SchnorrAggregator::complete`: either reject `len == 0` inside `SchnorrAggregate::read`, or return `false` early in `SchnorrAggregate::verify` when `keys_and_challenges.is_empty()`. Rejecting in `read` is preferable since it removes the invalid object entirely rather than relying on every `verify` caller to also have a non-empty signer set.

### Proof of Concept
```rust
// crypto/schnorr context, e.g. Ristretto
// Forged aggregate: 0 nonces, s = 0
let mut forged = vec![0u8; 4]; // u32 LE length = 0
forged.extend([0u8; 32]);      // canonical zero scalar

let agg = SchnorrAggregate::<Ristretto>::read::<&[u8]>(&mut forged.as_ref()).unwrap();
// Complete() would have refused to produce this (sigs.is_empty() -> None),
// yet the deserialized object verifies:
assert!(agg.verify(b"some-dst", &[])); // multiexp of [(-0, G)] == identity -> true
```
The equivalent path in `Validators::verify_aggregate` (`coordinator/tributary/src/tendermint/mod.rs:207-228`) passes `signers.len() == Rs.len() == 0`, builds zero challenges, and returns `aggregate.verify(DST, &[]) == true` for the same byte string.