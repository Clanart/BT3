# Q4948: NewFK20: spec-fk20srs via ComputeCellsAndKZGProofs [when/this]

## Question
Can an unprivileged attacker call `Context.ComputeCellsAndKZGProofs` (arguments: blob) with the first coset where padToPowerOfTwo adds an identity point, when the input is submitted inside a multi-member batch, so that NewFK20 reverses and truncates the monomial SRS with srs[evalSetSize:] and takeEveryNth; an off-by-one in the truncation makes emitted proofs diverge from the consensus-spec compute_cells_and_kzg_proofs, making the library use inconsistent values for `this client's accept set` and `c-kzg's accept set for the identical bytes` and breaking the invariant that the emitted cells/proofs/commitment equal the consensus-spec and c-kzg output for the same blob and trusted setup? Verify this specifically the batch decision must match the per-member decision, and assert `this client's accept set` equals `c-kzg's accept set for the identical bytes`.

## Target
- File/function: `internal/kzg_multi/fk20/fk20.go` -> `NewFK20`
- Entrypoint: `Context.ComputeCellsAndKZGProofs` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: the first coset where padToPowerOfTwo adds an identity point
- Exploit idea: NewFK20 reverses and truncates the monomial SRS with srs[evalSetSize:] and takeEveryNth; an off-by-one in the truncation makes emitted proofs diverge from the consensus-spec compute_cells_and_kzg_proofs. Construct the first coset where padToPowerOfTwo adds an identity point and route it through `Context.ComputeCellsAndKZGProofs` into `NewFK20` (internal/kzg_multi/fk20/fk20.go); the batch decision must match the per-member decision. The witness is the gap between `this client's accept set` and `c-kzg's accept set for the identical bytes`.
- Invariant to test: the emitted cells/proofs/commitment equal the consensus-spec and c-kzg output for the same blob and trusted setup
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `NewFK20` in `internal/kzg_multi/fk20/fk20.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
