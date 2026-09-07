# Q1550: deserializeG1Point: canon-flags via DeserializeKZGCommitment [under/the]

## Question
Can an unprivileged attacker call `DeserializeKZGCommitment` (arguments: commitment bytes) with an encoding with reserved flag bits set that gnark ignores, under concurrent invocation sharing the sync.Pool buffers, so that two distinct 48-byte encodings deserialize to the same G1 element because compression/sort flag combinations are tolerated, breaking the uniqueness half of canonicity, making the library use inconsistent values for `the deserialization success flag` and `whether the bytes are the unique canonical subgroup/scalar encoding` and breaking the invariant that deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b? Verify this specifically purity must hold despite pooling, and assert `the deserialization success flag` equals `whether the bytes are the unique canonical subgroup/scalar encoding`.

## Target
- File/function: `serialization.go` -> `deserializeG1Point`
- Entrypoint: `DeserializeKZGCommitment` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: an encoding with reserved flag bits set that gnark ignores
- Exploit idea: Two distinct 48-byte encodings deserialize to the same G1 element because compression/sort flag combinations are tolerated, breaking the uniqueness half of canonicity. Construct an encoding with reserved flag bits set that gnark ignores and route it through `DeserializeKZGCommitment` into `deserializeG1Point` (serialization.go); purity must hold despite pooling. The witness is the gap between `the deserialization success flag` and `whether the bytes are the unique canonical subgroup/scalar encoding`.
- Invariant to test: deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b
- Expected Immunefi impact: Critical - a proof, blob, or cell batch is accepted that is not a true opening (forged data availability / consensus split), or out-of-subgroup / non-canonical bytes deserialize and then pass verification
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `deserializeG1Point` in `serialization.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
