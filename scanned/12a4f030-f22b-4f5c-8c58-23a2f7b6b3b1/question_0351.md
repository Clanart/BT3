# Q0351: Verify: sound-zpoint via VerifyKZGProof [after/the]

## Question
Can an unprivileged attacker call `Context.VerifyKZGProof` (arguments: blobCommitment, inputPointBytes, claimedValueBytes, kzgProof) with inputPointBytes decoding to a scalar equal to the trusted-setup secret's image so alpha-z vanishes, after the deserialization guard returns success, so that the input point z is chosen so that [alpha - z]G2 becomes the identity or a low-order element, collapsing one pairing factor and letting a forged (claimedValue, quotient) pass, making the library use inconsistent values for `the PairingCheck outcome` and `a from-scratch pairing of the same operands` and breaking the invariant that Verify returns nil <=> the polynomial committed by the commitment evaluates to the claimed value at the input point? Verify this specifically the guard passed but the invariant still breaks, and assert `the PairingCheck outcome` equals `a from-scratch pairing of the same operands`.

## Target
- File/function: `internal/kzg/kzg_verify.go` -> `Verify`
- Entrypoint: `Context.VerifyKZGProof` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: inputPointBytes decoding to a scalar equal to the trusted-setup secret's image so alpha-z vanishes
- Exploit idea: The input point z is chosen so that [alpha - z]G2 becomes the identity or a low-order element, collapsing one pairing factor and letting a forged (claimedValue, quotient) pass. Construct inputPointBytes decoding to a scalar equal to the trusted-setup secret's image so alpha-z vanishes and route it through `Context.VerifyKZGProof` into `Verify` (internal/kzg/kzg_verify.go); the guard passed but the invariant still breaks. The witness is the gap between `the PairingCheck outcome` and `a from-scratch pairing of the same operands`.
- Invariant to test: Verify returns nil <=> the polynomial committed by the commitment evaluates to the claimed value at the input point
- Expected Immunefi impact: Critical - a proof, blob, or cell batch is accepted that is not a true opening (forged data availability / consensus split), or out-of-subgroup / non-canonical bytes deserialize and then pass verification
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `Verify` in `internal/kzg/kzg_verify.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
