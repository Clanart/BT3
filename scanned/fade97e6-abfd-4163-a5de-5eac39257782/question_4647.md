# Q4647: recoverPolynomialCoeffs: recover-ascending via RecoverCells [under/recovered[cellIDs[i]]]

## Question
Can an unprivileged attacker call `Context.RecoverCells` (arguments: cellIDs, cells) with a boundary id set of exactly NumBlocksNeededToReconstruct entries, under concurrent invocation sharing the sync.Pool buffers, so that isAscending and the cellID<CellsPerExtBlob checks admit an id set that passes but interacts badly with BitReverseInt on missing indices, so the vanishing polynomial is built on the wrong roots, making the library use inconsistent values for `recovered[cellIDs[i]]` and `the supplied cells[i]` and breaking the invariant that for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error? Verify this specifically purity must hold despite pooling, and assert `recovered[cellIDs[i]]` equals `the supplied cells[i]`.

## Target
- File/function: `api_eip7594.go` -> `recoverPolynomialCoeffs`
- Entrypoint: `Context.RecoverCells` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a boundary id set of exactly NumBlocksNeededToReconstruct entries
- Exploit idea: IsAscending and the cellID<CellsPerExtBlob checks admit an id set that passes but interacts badly with BitReverseInt on missing indices, so the vanishing polynomial is built on the wrong roots. Construct a boundary id set of exactly NumBlocksNeededToReconstruct entries and route it through `Context.RecoverCells` into `recoverPolynomialCoeffs` (api_eip7594.go); purity must hold despite pooling. The witness is the gap between `recovered[cellIDs[i]]` and `the supplied cells[i]`.
- Invariant to test: for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `recoverPolynomialCoeffs` in `api_eip7594.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
