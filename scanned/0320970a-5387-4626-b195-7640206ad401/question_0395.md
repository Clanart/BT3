# Q0395: Verify: sound-zpoint via VerifyKZGProof [when/Verify's]

## Question
Can an unprivileged attacker call `Context.VerifyKZGProof` (arguments: blobCommitment, inputPointBytes, claimedValueBytes, kzgProof) with inputPointBytes equal to a bit-reversed domain root, when compared against the consensus-spec test vectors in tests/, so that the input point z is chosen so that [alpha - z]G2 becomes the identity or a low-order element, collapsing one pairing factor and letting a forged (claimedValue, quotient) pass, making the library use inconsistent values for `Verify's boolean result` and `the true predicate f(inputPoint) == claimedValue recomputed independently` and breaking the invariant that Verify returns nil <=> the polynomial committed by the commitment evaluates to the claimed value at the input point? Verify this specifically the library output must equal the fixture, and assert `Verify's boolean result` equals `the true predicate f(inputPoint) == claimedValue recomputed independently`.

## Target
- File/function: `internal/kzg/kzg_verify.go` -> `Verify`
- Entrypoint: `Context.VerifyKZGProof` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: inputPointBytes equal to a bit-reversed domain root
- Exploit idea: The input point z is chosen so that [alpha - z]G2 becomes the identity or a low-order element, collapsing one pairing factor and letting a forged (claimedValue, quotient) pass. Construct inputPointBytes equal to a bit-reversed domain root and route it through `Context.VerifyKZGProof` into `Verify` (internal/kzg/kzg_verify.go); the library output must equal the fixture. The witness is the gap between `Verify's boolean result` and `the true predicate f(inputPoint) == claimedValue recomputed independently`.
- Invariant to test: Verify returns nil <=> the polynomial committed by the commitment evaluates to the claimed value at the input point
- Expected Immunefi impact: Critical - a proof, blob, or cell batch is accepted that is not a true opening (forged data availability / consensus split), or out-of-subgroup / non-canonical bytes deserialize and then pass verification
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `Verify` in `internal/kzg/kzg_verify.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
