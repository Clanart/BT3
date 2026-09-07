# Q0212: VerifyBlobKZGProofBatch: batch-empty via VerifyBlobKZGProofBatch [before/the]

## Question
Can an unprivileged attacker call `Context.VerifyBlobKZGProofBatch` (arguments: blobs, commitments, proofs) with blobs, commitments and proofs all empty slices, before verification enforces canonicity, so that a zero-length batch returns nil, which a caller treating nil as 'all proofs valid' interprets as acceptance of an empty availability claim, making the library use inconsistent values for `the batch's returned error` and `ErrBatchLengthCheck on any length mismatch` and breaking the invariant that a batch is accepted only when all designated component slices have equal length, and an empty batch is not silently treated as all-valid? Verify this specifically the divergence occurs before any guard runs, and assert `the batch's returned error` equals `ErrBatchLengthCheck on any length mismatch`.

## Target
- File/function: `verify.go` -> `VerifyBlobKZGProofBatch`
- Entrypoint: `Context.VerifyBlobKZGProofBatch` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: blobs, commitments and proofs all empty slices
- Exploit idea: A zero-length batch returns nil, which a caller treating nil as 'all proofs valid' interprets as acceptance of an empty availability claim. Construct blobs, commitments and proofs all empty slices and route it through `Context.VerifyBlobKZGProofBatch` into `VerifyBlobKZGProofBatch` (verify.go); the divergence occurs before any guard runs. The witness is the gap between `the batch's returned error` and `ErrBatchLengthCheck on any length mismatch`.
- Invariant to test: a batch is accepted only when all designated component slices have equal length, and an empty batch is not silently treated as all-valid
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `VerifyBlobKZGProofBatch` in `verify.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
