# Q4123: VerifyBlobKZGProofBatchPar: pool-concurrent via VerifyBlobKZGProofBatchPar [when/the]

## Question
Can an unprivileged attacker call `Context.VerifyBlobKZGProofBatchPar` (arguments: blobs, commitments, proofs) with a large parallel batch stressing the shared pools, when the field element is at the modulus boundary, so that concurrent VerifyBlobKZGProof goroutines share polynomialPool and elementSlicePool, so a data race lets one blob's evaluation influence another's accept/reject decision, making the library use inconsistent values for `the method result with a warm pool` and `the same call's result with a cold pool` and breaking the invariant that the result of every public Context method is a pure function of its arguments and the trusted setup, independent of prior or concurrent calls? Verify this specifically the canonical-scalar equality must still hold, and assert `the method result with a warm pool` equals `the same call's result with a cold pool`.

## Target
- File/function: `verify.go` -> `VerifyBlobKZGProofBatchPar`
- Entrypoint: `Context.VerifyBlobKZGProofBatchPar` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a large parallel batch stressing the shared pools
- Exploit idea: Concurrent VerifyBlobKZGProof goroutines share polynomialPool and elementSlicePool, so a data race lets one blob's evaluation influence another's accept/reject decision. Construct a large parallel batch stressing the shared pools and route it through `Context.VerifyBlobKZGProofBatchPar` into `VerifyBlobKZGProofBatchPar` (verify.go); the canonical-scalar equality must still hold. The witness is the gap between `the method result with a warm pool` and `the same call's result with a cold pool`.
- Invariant to test: the result of every public Context method is a pure function of its arguments and the trusted setup, independent of prior or concurrent calls
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `VerifyBlobKZGProofBatchPar` in `verify.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
