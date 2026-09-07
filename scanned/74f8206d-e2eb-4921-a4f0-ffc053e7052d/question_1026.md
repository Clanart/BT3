# Q1026: deduplicateKZGCommitments: cell-dedupmap via VerifyCellKZGProofBatch [when/the]

## Question
Can an unprivileged attacker call `Context.VerifyCellKZGProofBatch` (arguments: commitments, cellIndices, cells, proofs) with a commitment multiset whose map iteration order changes the index assignment, when the field element is at the modulus boundary, so that deduplicateKZGCommitments reorders commitments relative to cellIndices so a proof is checked against a different row commitment than the cell belongs to, making the library use inconsistent values for `the rowIndices-to-cellIndices pairing` and `the intended cell-to-commitment mapping` and breaking the invariant that deduplicateKZGCommitments preserves the multiset: rebuilding from (deduplicated, indices) reproduces the original commitments in order? Verify this specifically the canonical-scalar equality must still hold, and assert `the rowIndices-to-cellIndices pairing` equals `the intended cell-to-commitment mapping`.

## Target
- File/function: `api_eip7594.go` -> `deduplicateKZGCommitments`
- Entrypoint: `Context.VerifyCellKZGProofBatch` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a commitment multiset whose map iteration order changes the index assignment
- Exploit idea: DeduplicateKZGCommitments reorders commitments relative to cellIndices so a proof is checked against a different row commitment than the cell belongs to. Construct a commitment multiset whose map iteration order changes the index assignment and route it through `Context.VerifyCellKZGProofBatch` into `deduplicateKZGCommitments` (api_eip7594.go); the canonical-scalar equality must still hold. The witness is the gap between `the rowIndices-to-cellIndices pairing` and `the intended cell-to-commitment mapping`.
- Invariant to test: deduplicateKZGCommitments preserves the multiset: rebuilding from (deduplicated, indices) reproduces the original commitments in order
- Expected Immunefi impact: Critical - a proof, blob, or cell batch is accepted that is not a true opening (forged data availability / consensus split), or out-of-subgroup / non-canonical bytes deserialize and then pass verification
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `deduplicateKZGCommitments` in `api_eip7594.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
