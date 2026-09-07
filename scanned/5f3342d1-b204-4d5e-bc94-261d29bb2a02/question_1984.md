# Q1984: VerifyMultiPointKZGProofBatch: cell-interp via VerifyCellKZGProofBatch [before/the]

## Question
Can an unprivileged attacker call `Context.VerifyCellKZGProofBatch` (arguments: commitments, cellIndices, cells, proofs) with a mix of cosets whose PolyAdd truncates the aggregate, before verification enforces canonicity, so that the interpolationPoly built by PolyAdd is shorter than cosetSize or drops coefficients, so commRandomSumInterPoly commits to the wrong polynomial and a mismatched cell passes, making the library use inconsistent values for `the multi-point pairing outcome` and `a per-coset re-derivation of each proof opening` and breaking the invariant that a cell is accepted <=> it equals the committed polynomial's evaluations on that coset, and its proof opens exactly that? Verify this specifically the divergence occurs before any guard runs, and assert `the multi-point pairing outcome` equals `a per-coset re-derivation of each proof opening`.

## Target
- File/function: `internal/kzg_multi/kzg_verify.go` -> `VerifyMultiPointKZGProofBatch`
- Entrypoint: `Context.VerifyCellKZGProofBatch` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a mix of cosets whose PolyAdd truncates the aggregate
- Exploit idea: The interpolationPoly built by PolyAdd is shorter than cosetSize or drops coefficients, so commRandomSumInterPoly commits to the wrong polynomial and a mismatched cell passes. Construct a mix of cosets whose PolyAdd truncates the aggregate and route it through `Context.VerifyCellKZGProofBatch` into `VerifyMultiPointKZGProofBatch` (internal/kzg_multi/kzg_verify.go); the divergence occurs before any guard runs. The witness is the gap between `the multi-point pairing outcome` and `a per-coset re-derivation of each proof opening`.
- Invariant to test: a cell is accepted <=> it equals the committed polynomial's evaluations on that coset, and its proof opens exactly that
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `VerifyMultiPointKZGProofBatch` in `internal/kzg_multi/kzg_verify.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
