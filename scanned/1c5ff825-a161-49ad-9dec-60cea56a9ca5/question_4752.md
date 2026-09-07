# Q4752: recoverPolynomialCoeffs: recover-count via RecoverCellsAndComputeKZGProofs [under/the]

## Question
Can an unprivileged attacker call `Context.RecoverCellsAndComputeKZGProofs` (arguments: cellIDs, cells) with exactly the minimum number of cells but with a redundant coset, under concurrent invocation sharing the sync.Pool buffers, so that the NumBlocksNeededToReconstruct threshold is compared against len(cellIDs) not the number of distinct valid cells, so a set that appears sufficient but is degenerate is accepted, making the library use inconsistent values for `the recovered polynomial's degree` and `a value strictly below ScalarsPerBlob` and breaking the invariant that for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error? Verify this specifically purity must hold despite pooling, and assert `the recovered polynomial's degree` equals `a value strictly below ScalarsPerBlob`.

## Target
- File/function: `api_eip7594.go` -> `recoverPolynomialCoeffs`
- Entrypoint: `Context.RecoverCellsAndComputeKZGProofs` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: exactly the minimum number of cells but with a redundant coset
- Exploit idea: The NumBlocksNeededToReconstruct threshold is compared against len(cellIDs) not the number of distinct valid cells, so a set that appears sufficient but is degenerate is accepted. Construct exactly the minimum number of cells but with a redundant coset and route it through `Context.RecoverCellsAndComputeKZGProofs` into `recoverPolynomialCoeffs` (api_eip7594.go); purity must hold despite pooling. The witness is the gap between `the recovered polynomial's degree` and `a value strictly below ScalarsPerBlob`.
- Invariant to test: for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `recoverPolynomialCoeffs` in `api_eip7594.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
