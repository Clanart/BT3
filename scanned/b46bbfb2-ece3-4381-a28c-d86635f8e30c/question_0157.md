# Q0157: Verify: sound-inf via VerifyKZGProof [when/the]

## Question
Can an unprivileged attacker call `Context.VerifyKZGProof` (arguments: blobCommitment, inputPointBytes, claimedValueBytes, kzgProof) with both commitment and proof set to the point at infinity, when compared against c-kzg for the identical bytes, so that the deserialized commitment or quotient proof is the G1 point at infinity, which deserializeG1Point accepts (gnark decodes the 0xc0 compressed-infinity encoding), so the pairing check e([f(alpha)-f(z)]G1, -G2)*e(Q, [alpha-z]G2)==1 may hold for a value the attacker chose, making the library use inconsistent values for `the PairingCheck outcome` and `a from-scratch pairing of the same operands` and breaking the invariant that Verify returns nil <=> the polynomial committed by the commitment evaluates to the claimed value at the input point? Verify this specifically the two implementations must agree on accept/emit, and assert `the PairingCheck outcome` equals `a from-scratch pairing of the same operands`.

## Target
- File/function: `internal/kzg/kzg_verify.go` -> `Verify`
- Entrypoint: `Context.VerifyKZGProof` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: both commitment and proof set to the point at infinity
- Exploit idea: The deserialized commitment or quotient proof is the G1 point at infinity, which deserializeG1Point accepts (gnark decodes the 0xc0 compressed-infinity encoding), so the pairing check e([f(alpha)-f(z)]G1, -G2)*e(Q, [alpha-z]G2)==1 may hold for a value the attacker chose. Construct both commitment and proof set to the point at infinity and route it through `Context.VerifyKZGProof` into `Verify` (internal/kzg/kzg_verify.go); the two implementations must agree on accept/emit. The witness is the gap between `the PairingCheck outcome` and `a from-scratch pairing of the same operands`.
- Invariant to test: Verify returns nil <=> the polynomial committed by the commitment evaluates to the claimed value at the input point
- Expected Immunefi impact: Critical - a proof, blob, or cell batch is accepted that is not a true opening (forged data availability / consensus split), or out-of-subgroup / non-canonical bytes deserialize and then pass verification
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `Verify` in `internal/kzg/kzg_verify.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
