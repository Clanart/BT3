# Q5986: MultiExpG1: multiexp-cfg via VerifyBlobKZGProofBatch [when/the]

## Question
Can an unprivileged attacker call `Context.VerifyBlobKZGProofBatch` (arguments: blobs, commitments, proofs) with a batch whose MultiExp receives mismatched scalar and point lengths, when the same input is replayed across two calls, so that MultiExpG1 forwards numGoRoutines to gnark; a caller-reachable value at the 1024 boundary or a zero-length scalar/point mismatch changes the folded commitment used in a verification decision, making the library use inconsistent values for `the PairingCheck outcome` and `a from-scratch pairing of the same operands` and breaking the invariant that Verify returns nil <=> the polynomial committed by the commitment evaluates to the claimed value at the input point? Verify this specifically the second call must produce the identical decision, and assert `the PairingCheck outcome` equals `a from-scratch pairing of the same operands`.

## Target
- File/function: `internal/multiexp/multiexp.go` -> `MultiExpG1`
- Entrypoint: `Context.VerifyBlobKZGProofBatch` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a batch whose MultiExp receives mismatched scalar and point lengths
- Exploit idea: MultiExpG1 forwards numGoRoutines to gnark; a caller-reachable value at the 1024 boundary or a zero-length scalar/point mismatch changes the folded commitment used in a verification decision. Construct a batch whose MultiExp receives mismatched scalar and point lengths and route it through `Context.VerifyBlobKZGProofBatch` into `MultiExpG1` (internal/multiexp/multiexp.go); the second call must produce the identical decision. The witness is the gap between `the PairingCheck outcome` and `a from-scratch pairing of the same operands`.
- Invariant to test: Verify returns nil <=> the polynomial committed by the commitment evaluates to the claimed value at the input point
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `MultiExpG1` in `internal/multiexp/multiexp.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
