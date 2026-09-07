# Q1423: deserializeG1Point: canon-inf via VerifyKZGProof [before/Serialize(Deserialize(bytes))]

## Question
Can an unprivileged attacker call `Context.VerifyKZGProof` (arguments: blobCommitment, inputPointBytes, claimedValueBytes, kzgProof) with an uncompressed-infinity encoding routed through DeserializeKZGCommitment, before verification enforces canonicity, so that deserializeG1Point accepts a compressed-infinity encoding, so a commitment or proof of the point at infinity enters verification where the spec's validate_kzg_g1 semantics may differ, making the library use inconsistent values for `Serialize(Deserialize(bytes))` and `the original input bytes` and breaking the invariant that deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b? Verify this specifically the divergence occurs before any guard runs, and assert `Serialize(Deserialize(bytes))` equals `the original input bytes`.

## Target
- File/function: `serialization.go` -> `deserializeG1Point`
- Entrypoint: `Context.VerifyKZGProof` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: an uncompressed-infinity encoding routed through DeserializeKZGCommitment
- Exploit idea: DeserializeG1Point accepts a compressed-infinity encoding, so a commitment or proof of the point at infinity enters verification where the spec's validate_kzg_g1 semantics may differ. Construct an uncompressed-infinity encoding routed through DeserializeKZGCommitment and route it through `Context.VerifyKZGProof` into `deserializeG1Point` (serialization.go); the divergence occurs before any guard runs. The witness is the gap between `Serialize(Deserialize(bytes))` and `the original input bytes`.
- Invariant to test: deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b
- Expected Immunefi impact: Critical - a proof, blob, or cell batch is accepted that is not a true opening (forged data availability / consensus split), or out-of-subgroup / non-canonical bytes deserialize and then pass verification
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `deserializeG1Point` in `serialization.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
