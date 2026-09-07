# Q2105: DeserializeScalar: canon-scalar via DeserializeScalar [under/the]

## Question
Can an unprivileged attacker call `DeserializeScalar` (arguments: scalar bytes) with a value equal to BLS_MODULUS + small, under concurrent invocation sharing the sync.Pool buffers, so that DeserializeScalar / ReduceCanonicalBigEndian accepts a non-canonical scalar (>= BLS_MODULUS) or maps two byte strings to one field element, so inputPointBytes or claimedValueBytes are ambiguous, making the library use inconsistent values for `the deserialization success flag` and `whether the bytes are the unique canonical subgroup/scalar encoding` and breaking the invariant that deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b? Verify this specifically purity must hold despite pooling, and assert `the deserialization success flag` equals `whether the bytes are the unique canonical subgroup/scalar encoding`.

## Target
- File/function: `serialization.go` -> `DeserializeScalar`
- Entrypoint: `DeserializeScalar` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a value equal to BLS_MODULUS + small
- Exploit idea: DeserializeScalar / ReduceCanonicalBigEndian accepts a non-canonical scalar (>= BLS_MODULUS) or maps two byte strings to one field element, so inputPointBytes or claimedValueBytes are ambiguous. Construct a value equal to BLS_MODULUS + small and route it through `DeserializeScalar` into `DeserializeScalar` (serialization.go); purity must hold despite pooling. The witness is the gap between `the deserialization success flag` and `whether the bytes are the unique canonical subgroup/scalar encoding`.
- Invariant to test: deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b
- Expected Immunefi impact: Critical - a proof, blob, or cell batch is accepted that is not a true opening (forged data availability / consensus split), or out-of-subgroup / non-canonical bytes deserialize and then pass verification
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `DeserializeScalar` in `serialization.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
