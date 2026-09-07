# Q5638: NewContext4096: setup-lagrange via NewContext4096 [when/this]

## Question
Can an unprivileged attacker call `NewContext4096` (arguments: trustedSetup JSON) with a setup with consistent-looking but mismatched bases, when the byte length is exactly at the type's fixed-size boundary, so that NewContext4096 never confirms SetupG1Lagrange is the IFFT of SetupG1Monomial, so a caller-supplied JSON with mismatched Lagrange and monomial points produces commitments and proofs that disagree with each other and the spec, making the library use inconsistent values for `this client's accept set` and `c-kzg's accept set for the identical bytes` and breaking the invariant that the emitted cells/proofs/commitment equal the consensus-spec and c-kzg output for the same blob and trusted setup? Verify this specifically the fixed-size decode must not read stale or padding bytes, and assert `this client's accept set` equals `c-kzg's accept set for the identical bytes`.

## Target
- File/function: `api.go` -> `NewContext4096`
- Entrypoint: `NewContext4096` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a setup with consistent-looking but mismatched bases
- Exploit idea: NewContext4096 never confirms SetupG1Lagrange is the IFFT of SetupG1Monomial, so a caller-supplied JSON with mismatched Lagrange and monomial points produces commitments and proofs that disagree with each other and the spec. Construct a setup with consistent-looking but mismatched bases and route it through `NewContext4096` into `NewContext4096` (api.go); the fixed-size decode must not read stale or padding bytes. The witness is the gap between `this client's accept set` and `c-kzg's accept set for the identical bytes`.
- Invariant to test: the emitted cells/proofs/commitment equal the consensus-spec and c-kzg output for the same blob and trusted setup
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `NewContext4096` in `api.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
