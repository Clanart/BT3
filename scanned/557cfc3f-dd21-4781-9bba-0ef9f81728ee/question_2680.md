# Q2680: SerializeG1Point: canon-roundtrip via VerifyBlobKZGProof [before/Serialize(Deserialize(bytes))]

## Question
Can an unprivileged attacker call `Context.VerifyBlobKZGProof` (arguments: blob, blobCommitment, kzgProof) with a flag-variant encoding that does not round-trip, before verification enforces canonicity, so that SerializeG1Point(deserializeG1Point(b)) != b for some accepted b, so a commitment's canonical form used in the transcript differs from the bytes the attacker supplied, making the library use inconsistent values for `Serialize(Deserialize(bytes))` and `the original input bytes` and breaking the invariant that deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b? Verify this specifically the divergence occurs before any guard runs, and assert `Serialize(Deserialize(bytes))` equals `the original input bytes`.

## Target
- File/function: `serialization.go` -> `SerializeG1Point`
- Entrypoint: `Context.VerifyBlobKZGProof` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a flag-variant encoding that does not round-trip
- Exploit idea: SerializeG1Point(deserializeG1Point(b)) != b for some accepted b, so a commitment's canonical form used in the transcript differs from the bytes the attacker supplied. Construct a flag-variant encoding that does not round-trip and route it through `Context.VerifyBlobKZGProof` into `SerializeG1Point` (serialization.go); the divergence occurs before any guard runs. The witness is the gap between `Serialize(Deserialize(bytes))` and `the original input bytes`.
- Invariant to test: deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `SerializeG1Point` in `serialization.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
