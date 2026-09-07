# Q2304: deserializeBlobToPoly: canon-blobscalar via DeserializeBlob [after/Serialize(Deserialize(bytes))]

## Question
Can an unprivileged attacker call `DeserializeBlob` (arguments: blob bytes) with a blob whose 4096th scalar equals BLS_MODULUS, after the deserialization guard returns success, so that a blob chunk that is a non-canonical scalar is only rejected inside deserializeBlobToPoly after some earlier accept decision, or the SetBytesCanonical error path leaks a partially filled polynomial, making the library use inconsistent values for `Serialize(Deserialize(bytes))` and `the original input bytes` and breaking the invariant that deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b? Verify this specifically the guard passed but the invariant still breaks, and assert `Serialize(Deserialize(bytes))` equals `the original input bytes`.

## Target
- File/function: `serialization.go` -> `deserializeBlobToPoly`
- Entrypoint: `DeserializeBlob` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a blob whose 4096th scalar equals BLS_MODULUS
- Exploit idea: A blob chunk that is a non-canonical scalar is only rejected inside deserializeBlobToPoly after some earlier accept decision, or the SetBytesCanonical error path leaks a partially filled polynomial. Construct a blob whose 4096th scalar equals BLS_MODULUS and route it through `DeserializeBlob` into `deserializeBlobToPoly` (serialization.go); the guard passed but the invariant still breaks. The witness is the gap between `Serialize(Deserialize(bytes))` and `the original input bytes`.
- Invariant to test: deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `deserializeBlobToPoly` in `serialization.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
