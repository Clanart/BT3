# Q0669: BatchVerifyMultiPoints: batch-cancel via VerifyBlobKZGProofBatch [when/the]

## Question
Can an unprivileged attacker call `Context.VerifyBlobKZGProofBatch` (arguments: blobs, commitments, proofs) with a member with a commitment at infinity paired with a member with a proof at infinity, when the input is submitted inside a multi-member batch, so that two batch members contribute foldedCommitments / foldedQuotients terms that cancel under the random linear combination, so an invalid member is masked by a second crafted member, making the library use inconsistent values for `the folded pairing outcome` and `the conjunction of the unfolded per-member pairings` and breaking the invariant that batch verification accepts <=> every member (commitment_i, proof_i, input_i) would accept individually? Verify this specifically the batch decision must match the per-member decision, and assert `the folded pairing outcome` equals `the conjunction of the unfolded per-member pairings`.

## Target
- File/function: `internal/kzg/kzg_verify.go` -> `BatchVerifyMultiPoints`
- Entrypoint: `Context.VerifyBlobKZGProofBatch` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a member with a commitment at infinity paired with a member with a proof at infinity
- Exploit idea: Two batch members contribute foldedCommitments / foldedQuotients terms that cancel under the random linear combination, so an invalid member is masked by a second crafted member. Construct a member with a commitment at infinity paired with a member with a proof at infinity and route it through `Context.VerifyBlobKZGProofBatch` into `BatchVerifyMultiPoints` (internal/kzg/kzg_verify.go); the batch decision must match the per-member decision. The witness is the gap between `the folded pairing outcome` and `the conjunction of the unfolded per-member pairings`.
- Invariant to test: batch verification accepts <=> every member (commitment_i, proof_i, input_i) would accept individually
- Expected Immunefi impact: Critical - a proof, blob, or cell batch is accepted that is not a true opening (forged data availability / consensus split), or out-of-subgroup / non-canonical bytes deserialize and then pass verification
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `BatchVerifyMultiPoints` in `internal/kzg/kzg_verify.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
