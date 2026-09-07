# Q0530: Verify: sound-claim via VerifyKZGProof [when/the]

## Question
Can an unprivileged attacker call `Context.VerifyKZGProof` (arguments: blobCommitment, inputPointBytes, claimedValueBytes, kzgProof) with a claimedValue of zero with a quotient equal to the commitment, when the input is submitted inside a multi-member batch, so that claimedValueBytes and the quotient commitment are jointly crafted so [f(alpha)-f(z)]G1 and Q satisfy the pairing without f actually opening to claimedValue at z, making the library use inconsistent values for `the PairingCheck outcome` and `a from-scratch pairing of the same operands` and breaking the invariant that Verify returns nil <=> the polynomial committed by the commitment evaluates to the claimed value at the input point? Verify this specifically the batch decision must match the per-member decision, and assert `the PairingCheck outcome` equals `a from-scratch pairing of the same operands`.

## Target
- File/function: `internal/kzg/kzg_verify.go` -> `Verify`
- Entrypoint: `Context.VerifyKZGProof` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a claimedValue of zero with a quotient equal to the commitment
- Exploit idea: ClaimedValueBytes and the quotient commitment are jointly crafted so [f(alpha)-f(z)]G1 and Q satisfy the pairing without f actually opening to claimedValue at z. Construct a claimedValue of zero with a quotient equal to the commitment and route it through `Context.VerifyKZGProof` into `Verify` (internal/kzg/kzg_verify.go); the batch decision must match the per-member decision. The witness is the gap between `the PairingCheck outcome` and `a from-scratch pairing of the same operands`.
- Invariant to test: Verify returns nil <=> the polynomial committed by the commitment evaluates to the claimed value at the input point
- Expected Immunefi impact: Critical - a proof, blob, or cell batch is accepted that is not a true opening (forged data availability / consensus split), or out-of-subgroup / non-canonical bytes deserialize and then pass verification
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `Verify` in `internal/kzg/kzg_verify.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
