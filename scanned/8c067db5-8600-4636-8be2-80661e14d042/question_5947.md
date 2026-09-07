# Q5947: PolyMul: poly-trailingzero via RecoverCellsAndComputeKZGProofs [under/recovered[cellIDs[i]]]

## Question
Can an unprivileged attacker call `Context.RecoverCellsAndComputeKZGProofs` (arguments: cellIDs, cells) with an erasure set whose product normalizes to a shorter polynomial, under concurrent invocation sharing the sync.Pool buffers, so that removeTrailingZeros in PolyMul normalizes away high zero coefficients, so a vanishing polynomial built in RecoverPolynomialCoefficients loses degree information for a crafted erasure set, making the library use inconsistent values for `recovered[cellIDs[i]]` and `the supplied cells[i]` and breaking the invariant that for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error? Verify this specifically purity must hold despite pooling, and assert `recovered[cellIDs[i]]` equals `the supplied cells[i]`.

## Target
- File/function: `internal/poly/poly.go` -> `PolyMul`
- Entrypoint: `Context.RecoverCellsAndComputeKZGProofs` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: an erasure set whose product normalizes to a shorter polynomial
- Exploit idea: RemoveTrailingZeros in PolyMul normalizes away high zero coefficients, so a vanishing polynomial built in RecoverPolynomialCoefficients loses degree information for a crafted erasure set. Construct an erasure set whose product normalizes to a shorter polynomial and route it through `Context.RecoverCellsAndComputeKZGProofs` into `PolyMul` (internal/poly/poly.go); purity must hold despite pooling. The witness is the gap between `recovered[cellIDs[i]]` and `the supplied cells[i]`.
- Invariant to test: for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `PolyMul` in `internal/poly/poly.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
