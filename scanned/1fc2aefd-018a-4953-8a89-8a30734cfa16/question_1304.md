# Q1304: VerifyBlobKZGProofBatchPar: batchpar-err via VerifyBlobKZGProofBatchPar [when/the]

## Question
Can an unprivileged attacker call `Context.VerifyBlobKZGProofBatchPar` (arguments: blobs, commitments, proofs) with a batch where one member deserialization error is dropped by a goroutine, when compared against c-kzg for the identical bytes, so that the errgroup parallel path returns nil if a per-blob VerifyBlobKZGProof swallows or races an error, so one invalid member does not fail the batch as VerifyBlobKZGProofBatch would, making the library use inconsistent values for `the folded pairing outcome` and `the conjunction of the unfolded per-member pairings` and breaking the invariant that batch verification accepts <=> every member (commitment_i, proof_i, input_i) would accept individually? Verify this specifically the two implementations must agree on accept/emit, and assert `the folded pairing outcome` equals `the conjunction of the unfolded per-member pairings`.

## Target
- File/function: `verify.go` -> `VerifyBlobKZGProofBatchPar`
- Entrypoint: `Context.VerifyBlobKZGProofBatchPar` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a batch where one member deserialization error is dropped by a goroutine
- Exploit idea: The errgroup parallel path returns nil if a per-blob VerifyBlobKZGProof swallows or races an error, so one invalid member does not fail the batch as VerifyBlobKZGProofBatch would. Construct a batch where one member deserialization error is dropped by a goroutine and route it through `Context.VerifyBlobKZGProofBatchPar` into `VerifyBlobKZGProofBatchPar` (verify.go); the two implementations must agree on accept/emit. The witness is the gap between `the folded pairing outcome` and `the conjunction of the unfolded per-member pairings`.
- Invariant to test: batch verification accepts <=> every member (commitment_i, proof_i, input_i) would accept individually
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `VerifyBlobKZGProofBatchPar` in `verify.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
