# Q0753: BatchVerifyMultiPoints: batch-rand via VerifyBlobKZGProofBatch [under/the]

## Question
Can an unprivileged attacker call `Context.VerifyBlobKZGProofBatch` (arguments: blobs, commitments, proofs) with a batch sized so ComputePowers wraps to a repeated power (e.g. randomNumber a low-order root), under concurrent invocation sharing the sync.Pool buffers, so that the randomNumbers powers from ComputePowers(randomNumber, batchSize) are not injective for a chosen batchSize or randomNumber==0/1 edge, so distinct members collapse to equal weights and an invalid member passes, making the library use inconsistent values for `the batch result` and `the AND of per-member VerifyKZGProof results` and breaking the invariant that batch verification accepts <=> every member (commitment_i, proof_i, input_i) would accept individually? Verify this specifically purity must hold despite pooling, and assert `the batch result` equals `the AND of per-member VerifyKZGProof results`.

## Target
- File/function: `internal/kzg/kzg_verify.go` -> `BatchVerifyMultiPoints`
- Entrypoint: `Context.VerifyBlobKZGProofBatch` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a batch sized so ComputePowers wraps to a repeated power (e.g. randomNumber a low-order root)
- Exploit idea: The randomNumbers powers from ComputePowers(randomNumber, batchSize) are not injective for a chosen batchSize or randomNumber==0/1 edge, so distinct members collapse to equal weights and an invalid member passes. Construct a batch sized so ComputePowers wraps to a repeated power (e.g. randomNumber a low-order root) and route it through `Context.VerifyBlobKZGProofBatch` into `BatchVerifyMultiPoints` (internal/kzg/kzg_verify.go); purity must hold despite pooling. The witness is the gap between `the batch result` and `the AND of per-member VerifyKZGProof results`.
- Invariant to test: batch verification accepts <=> every member (commitment_i, proof_i, input_i) would accept individually
- Expected Immunefi impact: Critical - a proof, blob, or cell batch is accepted that is not a true opening (forged data availability / consensus split), or out-of-subgroup / non-canonical bytes deserialize and then pass verification
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `BatchVerifyMultiPoints` in `internal/kzg/kzg_verify.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
