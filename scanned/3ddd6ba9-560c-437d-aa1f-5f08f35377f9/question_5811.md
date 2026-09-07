# Q5811: NewContext4096: setup-g2len via NewContext4096 [when/the]

## Question
Can an unprivileged attacker call `NewContext4096` (arguments: trustedSetup JSON) with a setup whose G2 length changes openKey7594's G2[cosetSize], when compared against c-kzg for the identical bytes, so that NewContext4096 checks len(SetupG2) >= 2 and panics when < scalarsPerCell, so a caller-supplied JSON with a short G2 list either constructs an unsound opening key or crashes on a public path, making the library use inconsistent values for `the batch's returned error` and `ErrBatchLengthCheck on any length mismatch` and breaking the invariant that a batch is accepted only when all designated component slices have equal length, and an empty batch is not silently treated as all-valid? Verify this specifically the two implementations must agree on accept/emit, and assert `the batch's returned error` equals `ErrBatchLengthCheck on any length mismatch`.

## Target
- File/function: `api.go` -> `NewContext4096`
- Entrypoint: `NewContext4096` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a setup whose G2 length changes openKey7594's G2[cosetSize]
- Exploit idea: NewContext4096 checks len(SetupG2) >= 2 and panics when < scalarsPerCell, so a caller-supplied JSON with a short G2 list either constructs an unsound opening key or crashes on a public path. Construct a setup whose G2 length changes openKey7594's G2[cosetSize] and route it through `NewContext4096` into `NewContext4096` (api.go); the two implementations must agree on accept/emit. The witness is the gap between `the batch's returned error` and `ErrBatchLengthCheck on any length mismatch`.
- Invariant to test: a batch is accepted only when all designated component slices have equal length, and an empty batch is not silently treated as all-valid
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `NewContext4096` in `api.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
