# Q4471: recoverPolynomialCoeffs: recover-supplied via RecoverCellsAndComputeKZGProofs [when/recovered[cellIDs[i]]]

## Question
Can an unprivileged attacker call `Context.RecoverCellsAndComputeKZGProofs` (arguments: cellIDs, cells) with an inconsistent set that recovers to a different codeword, when the value is reused immediately in a follow-on Compute call, so that the supplied cells are not asserted to survive recovery unchanged, so recovered[cellIDs[i]] != cells[i] for a crafted set and the node re-derives different cells than it was given, making the library use inconsistent values for `recovered[cellIDs[i]]` and `the supplied cells[i]` and breaking the invariant that for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error? Verify this specifically the follow-on output must not depend on the prior verify, and assert `recovered[cellIDs[i]]` equals `the supplied cells[i]`.

## Target
- File/function: `api_eip7594.go` -> `recoverPolynomialCoeffs`
- Entrypoint: `Context.RecoverCellsAndComputeKZGProofs` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: an inconsistent set that recovers to a different codeword
- Exploit idea: The supplied cells are not asserted to survive recovery unchanged, so recovered[cellIDs[i]] != cells[i] for a crafted set and the node re-derives different cells than it was given. Construct an inconsistent set that recovers to a different codeword and route it through `Context.RecoverCellsAndComputeKZGProofs` into `recoverPolynomialCoeffs` (api_eip7594.go); the follow-on output must not depend on the prior verify. The witness is the gap between `recovered[cellIDs[i]]` and `the supplied cells[i]`.
- Invariant to test: for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `recoverPolynomialCoeffs` in `api_eip7594.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
