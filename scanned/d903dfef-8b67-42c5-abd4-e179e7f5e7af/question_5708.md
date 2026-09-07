# Q5708: parseG1PointsNoSubgroupCheck: setup-nosubgroup via NewContext4096 [when/Serialize(Deserialize(bytes))]

## Question
Can an unprivileged attacker call `NewContext4096` (arguments: trustedSetup JSON) with a setup with a G2 point of small order, when compared against c-kzg for the identical bytes, so that parseTrustedSetup uses NoSubgroupChecks, so a caller-supplied JSON setup with an out-of-subgroup point is accepted into the commit/opening keys and every later proof inherits the flaw, making the library use inconsistent values for `Serialize(Deserialize(bytes))` and `the original input bytes` and breaking the invariant that deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b? Verify this specifically the two implementations must agree on accept/emit, and assert `Serialize(Deserialize(bytes))` equals `the original input bytes`.

## Target
- File/function: `trusted_setup.go` -> `parseG1PointsNoSubgroupCheck`
- Entrypoint: `NewContext4096` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a setup with a G2 point of small order
- Exploit idea: ParseTrustedSetup uses NoSubgroupChecks, so a caller-supplied JSON setup with an out-of-subgroup point is accepted into the commit/opening keys and every later proof inherits the flaw. Construct a setup with a G2 point of small order and route it through `NewContext4096` into `parseG1PointsNoSubgroupCheck` (trusted_setup.go); the two implementations must agree on accept/emit. The witness is the gap between `Serialize(Deserialize(bytes))` and `the original input bytes`.
- Invariant to test: deserialization succeeds <=> the bytes are the unique canonical encoding of a prime-order-subgroup element (or a scalar < BLS_MODULUS), and Serialize(Deserialize(b)) == b
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `parseG1PointsNoSubgroupCheck` in `trusted_setup.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
