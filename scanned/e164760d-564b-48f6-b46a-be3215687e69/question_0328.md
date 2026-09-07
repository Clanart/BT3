# Q0328: VerifyBlobKZGProofBatch: batch-empty via VerifyBlobKZGProofBatchPar [after/an]

## Question
Can an unprivileged attacker call `Context.VerifyBlobKZGProofBatchPar` (arguments: blobs, commitments, proofs) with blobs, commitments and proofs all empty slices, after the deserialization guard returns success, so that a zero-length batch returns nil, which a caller treating nil as 'all proofs valid' interprets as acceptance of an empty availability claim, making the library use inconsistent values for `an empty-batch result` and `an explicit no-op that a caller must not read as all-valid` and breaking the invariant that a batch is accepted only when all designated component slices have equal length, and an empty batch is not silently treated as all-valid? Verify this specifically the guard passed but the invariant still breaks, and assert `an empty-batch result` equals `an explicit no-op that a caller must not read as all-valid`.

## Target
- File/function: `verify.go` -> `VerifyBlobKZGProofBatch`
- Entrypoint: `Context.VerifyBlobKZGProofBatchPar` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: blobs, commitments and proofs all empty slices
- Exploit idea: A zero-length batch returns nil, which a caller treating nil as 'all proofs valid' interprets as acceptance of an empty availability claim. Construct blobs, commitments and proofs all empty slices and route it through `Context.VerifyBlobKZGProofBatchPar` into `VerifyBlobKZGProofBatch` (verify.go); the guard passed but the invariant still breaks. The witness is the gap between `an empty-batch result` and `an explicit no-op that a caller must not read as all-valid`.
- Invariant to test: a batch is accepted only when all designated component slices have equal length, and an empty batch is not silently treated as all-valid
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `VerifyBlobKZGProofBatch` in `verify.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
