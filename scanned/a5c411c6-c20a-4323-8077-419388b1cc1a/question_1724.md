# Q1724: VerifyCellKZGProofBatch: cell-rowidx via VerifyCellKZGProofBatch [when/the]

## Question
Can an unprivileged attacker call `Context.VerifyCellKZGProofBatch` (arguments: commitments, cellIndices, cells, proofs) with a rowIndex equal to len(rowCommitments) after dedup, when the field element is at the modulus boundary, so that the rowIndex or cellIndex bounds checks (ErrInvalidRowIndex, ErrInvalidCellID) are evaded so a cell references a coset or commitment outside the batch, making the library use inconsistent values for `the cell-batch result` and `per-cell equality of cells[k] with the committed polynomial on coset cellIndices[k]` and breaking the invariant that a cell is accepted <=> it equals the committed polynomial's evaluations on that coset, and its proof opens exactly that? Verify this specifically the canonical-scalar equality must still hold, and assert `the cell-batch result` equals `per-cell equality of cells[k] with the committed polynomial on coset cellIndices[k]`.

## Target
- File/function: `api_eip7594.go` -> `VerifyCellKZGProofBatch`
- Entrypoint: `Context.VerifyCellKZGProofBatch` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a rowIndex equal to len(rowCommitments) after dedup
- Exploit idea: The rowIndex or cellIndex bounds checks (ErrInvalidRowIndex, ErrInvalidCellID) are evaded so a cell references a coset or commitment outside the batch. Construct a rowIndex equal to len(rowCommitments) after dedup and route it through `Context.VerifyCellKZGProofBatch` into `VerifyCellKZGProofBatch` (api_eip7594.go); the canonical-scalar equality must still hold. The witness is the gap between `the cell-batch result` and `per-cell equality of cells[k] with the committed polynomial on coset cellIndices[k]`.
- Invariant to test: a cell is accepted <=> it equals the committed polynomial's evaluations on that coset, and its proof opens exactly that
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `VerifyCellKZGProofBatch` in `api_eip7594.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
