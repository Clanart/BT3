# Q4538: recoverPolynomialCoeffs: recover-ascending via RecoverCells [when/the]

## Question
Can an unprivileged attacker call `Context.RecoverCells` (arguments: cellIDs, cells) with a strictly ascending id set whose complement bit-reverses to collisions, when compared against c-kzg for the identical bytes, so that isAscending and the cellID<CellsPerExtBlob checks admit an id set that passes but interacts badly with BitReverseInt on missing indices, so the vanishing polynomial is built on the wrong roots, making the library use inconsistent values for `the recovered polynomial's degree` and `a value strictly below ScalarsPerBlob` and breaking the invariant that for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error? Verify this specifically the two implementations must agree on accept/emit, and assert `the recovered polynomial's degree` equals `a value strictly below ScalarsPerBlob`.

## Target
- File/function: `api_eip7594.go` -> `recoverPolynomialCoeffs`
- Entrypoint: `Context.RecoverCells` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a strictly ascending id set whose complement bit-reverses to collisions
- Exploit idea: IsAscending and the cellID<CellsPerExtBlob checks admit an id set that passes but interacts badly with BitReverseInt on missing indices, so the vanishing polynomial is built on the wrong roots. Construct a strictly ascending id set whose complement bit-reverses to collisions and route it through `Context.RecoverCells` into `recoverPolynomialCoeffs` (api_eip7594.go); the two implementations must agree on accept/emit. The witness is the gap between `the recovered polynomial's degree` and `a value strictly below ScalarsPerBlob`.
- Invariant to test: for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `recoverPolynomialCoeffs` in `api_eip7594.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
