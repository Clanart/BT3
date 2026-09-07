# Q5886: PolyAdd: poly-add via VerifyCellKZGProofBatch [when/the]

## Question
Can an unprivileged attacker call `Context.VerifyCellKZGProofBatch` (arguments: commitments, cellIndices, cells, proofs) with inputs of unequal length feeding CommitG1, when the input sits at a power-of-two / bit-reversal boundary, so that PolyAdd returns a slice sized to the longer input but a caller downstream assumes a fixed length, so an interpolation aggregate is shorter than cosetSize and cell verification commits to a truncated polynomial, making the library use inconsistent values for `the multi-point pairing outcome` and `a per-coset re-derivation of each proof opening` and breaking the invariant that a cell is accepted <=> it equals the committed polynomial's evaluations on that coset, and its proof opens exactly that? Verify this specifically the boundary case must match the spec output, and assert `the multi-point pairing outcome` equals `a per-coset re-derivation of each proof opening`.

## Target
- File/function: `internal/poly/poly.go` -> `PolyAdd`
- Entrypoint: `Context.VerifyCellKZGProofBatch` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: inputs of unequal length feeding CommitG1
- Exploit idea: PolyAdd returns a slice sized to the longer input but a caller downstream assumes a fixed length, so an interpolation aggregate is shorter than cosetSize and cell verification commits to a truncated polynomial. Construct inputs of unequal length feeding CommitG1 and route it through `Context.VerifyCellKZGProofBatch` into `PolyAdd` (internal/poly/poly.go); the boundary case must match the spec output. The witness is the gap between `the multi-point pairing outcome` and `a per-coset re-derivation of each proof opening`.
- Invariant to test: a cell is accepted <=> it equals the committed polynomial's evaluations on that coset, and its proof opens exactly that
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `PolyAdd` in `internal/poly/poly.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
