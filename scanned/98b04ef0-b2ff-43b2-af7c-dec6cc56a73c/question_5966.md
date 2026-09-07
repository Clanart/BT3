# Q5966: PolyMul: poly-trailingzero via RecoverCellsAndComputeKZGProofs [when/the]

## Question
Can an unprivileged attacker call `Context.RecoverCellsAndComputeKZGProofs` (arguments: cellIDs, cells) with roots arranged so PolyMul drops a leading term, when compared against the consensus-spec test vectors in tests/, so that removeTrailingZeros in PolyMul normalizes away high zero coefficients, so a vanishing polynomial built in RecoverPolynomialCoefficients loses degree information for a crafted erasure set, making the library use inconsistent values for `the recovered polynomial's degree` and `a value strictly below ScalarsPerBlob` and breaking the invariant that for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error? Verify this specifically the library output must equal the fixture, and assert `the recovered polynomial's degree` equals `a value strictly below ScalarsPerBlob`.

## Target
- File/function: `internal/poly/poly.go` -> `PolyMul`
- Entrypoint: `Context.RecoverCellsAndComputeKZGProofs` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: roots arranged so PolyMul drops a leading term
- Exploit idea: RemoveTrailingZeros in PolyMul normalizes away high zero coefficients, so a vanishing polynomial built in RecoverPolynomialCoefficients loses degree information for a crafted erasure set. Construct roots arranged so PolyMul drops a leading term and route it through `Context.RecoverCellsAndComputeKZGProofs` into `PolyMul` (internal/poly/poly.go); the library output must equal the fixture. The witness is the gap between `the recovered polynomial's degree` and `a value strictly below ScalarsPerBlob`.
- Invariant to test: for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `PolyMul` in `internal/poly/poly.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
