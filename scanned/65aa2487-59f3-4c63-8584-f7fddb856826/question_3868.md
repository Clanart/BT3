# Q3868: VerifyBlobKZGProofBatch: eval-aliasfree via VerifyBlobKZGProofBatch [under/the]

## Question
Can an unprivileged attacker call `Context.VerifyBlobKZGProofBatch` (arguments: blobs, commitments, proofs) with an evaluation returning &poly[index] just before the buffer is pooled, under concurrent invocation sharing the sync.Pool buffers, so that EvaluateLagrangePolynomial may return a pointer into the pooled polynomial when z is a domain root; if the claimedValue copy before putPolynomial is missed on any path, a later call overwrites it, making the library use inconsistent values for `the decision after a prior attacker call` and `the decision in isolation` and breaking the invariant that the result of every public Context method is a pure function of its arguments and the trusted setup, independent of prior or concurrent calls? Verify this specifically purity must hold despite pooling, and assert `the decision after a prior attacker call` equals `the decision in isolation`.

## Target
- File/function: `verify.go` -> `VerifyBlobKZGProofBatch`
- Entrypoint: `Context.VerifyBlobKZGProofBatch` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: an evaluation returning &poly[index] just before the buffer is pooled
- Exploit idea: EvaluateLagrangePolynomial may return a pointer into the pooled polynomial when z is a domain root; if the claimedValue copy before putPolynomial is missed on any path, a later call overwrites it. Construct an evaluation returning &poly[index] just before the buffer is pooled and route it through `Context.VerifyBlobKZGProofBatch` into `VerifyBlobKZGProofBatch` (verify.go); purity must hold despite pooling. The witness is the gap between `the decision after a prior attacker call` and `the decision in isolation`.
- Invariant to test: the result of every public Context method is a pure function of its arguments and the trusted setup, independent of prior or concurrent calls
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `VerifyBlobKZGProofBatch` in `verify.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
