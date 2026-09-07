# Q1855: deserializeG1Point: canon-subgroup via VerifyKZGProof [when/Serialize(Deserialize(bytes))]

## Question
Can an unprivileged attacker call `Context.VerifyKZGProof` (arguments: blobCommitment, inputPointBytes, claimedValueBytes, kzgProof) with a commitment whose subgroup membership is never checked before pairing, when compared against c-kzg for the identical bytes, so that a G1 encoding that is on-curve but not in the prime-order subgroup is accepted because the subgroup check is skipped or delegated incorrectly, letting a torsion point enter the pairing, making the library use inconsistent values for `Serialize(Deserialize(bytes))` and `the original input bytes` and breaking the invariant that deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b? Verify this specifically the two implementations must agree on accept/emit, and assert `Serialize(Deserialize(bytes))` equals `the original input bytes`.

## Target
- File/function: `serialization.go` -> `deserializeG1Point`
- Entrypoint: `Context.VerifyKZGProof` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a commitment whose subgroup membership is never checked before pairing
- Exploit idea: A G1 encoding that is on-curve but not in the prime-order subgroup is accepted because the subgroup check is skipped or delegated incorrectly, letting a torsion point enter the pairing. Construct a commitment whose subgroup membership is never checked before pairing and route it through `Context.VerifyKZGProof` into `deserializeG1Point` (serialization.go); the two implementations must agree on accept/emit. The witness is the gap between `Serialize(Deserialize(bytes))` and `the original input bytes`.
- Invariant to test: deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b
- Expected Immunefi impact: Critical - a proof, blob, or cell batch is accepted that is not a true opening (forged data availability / consensus split), or out-of-subgroup / non-canonical bytes deserialize and then pass verification
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `deserializeG1Point` in `serialization.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
