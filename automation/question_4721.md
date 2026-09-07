# Q4721: RecoverPolynomialCoefficients: recover-cosetzero via RecoverCellsAndComputeKZGProofs [when/recovered[cellIDs[i]]]

## Question
Can an unprivileged attacker call `Context.RecoverCellsAndComputeKZGProofs` (arguments: cellIDs, cells) with an erasure pattern hitting the coset shift, when the field element is at the modulus boundary, so that a zero in cosetZxEval before fr.BatchInvert is silently skipped, so the quotient on the coset is wrong for a crafted missing-index pattern and recovery yields a polynomial that mismatches the data, making the library use inconsistent values for `recovered[cellIDs[i]]` and `the supplied cells[i]` and breaking the invariant that for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error? Verify this specifically the canonical-scalar equality must still hold, and assert `recovered[cellIDs[i]]` equals `the supplied cells[i]`.

## Target
- File/function: `internal/erasure_code/erasure_code.go` -> `RecoverPolynomialCoefficients`
- Entrypoint: `Context.RecoverCellsAndComputeKZGProofs` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: an erasure pattern hitting the coset shift
- Exploit idea: A zero in cosetZxEval before fr.BatchInvert is silently skipped, so the quotient on the coset is wrong for a crafted missing-index pattern and recovery yields a polynomial that mismatches the data. Construct an erasure pattern hitting the coset shift and route it through `Context.RecoverCellsAndComputeKZGProofs` into `RecoverPolynomialCoefficients` (internal/erasure_code/erasure_code.go); the canonical-scalar equality must still hold. The witness is the gap between `recovered[cellIDs[i]]` and `the supplied cells[i]`.
- Invariant to test: for every supplied cellIDs[i], recovered[cellIDs[i]] == cells[i] and the recovered polynomial has degree < ScalarsPerBlob, else an error
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `RecoverPolynomialCoefficients` in `internal/erasure_code/erasure_code.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
