# Q4894: NewFK20: spec-fk20srs via ComputeCellsAndKZGProofs [when/this]

## Question
Can an unprivileged attacker call `Context.ComputeCellsAndKZGProofs` (arguments: blob) with a blob whose proofs expose the srs[evalSetSize:] boundary, when the same input is replayed across two calls, so that NewFK20 reverses and truncates the monomial SRS with srs[evalSetSize:] and takeEveryNth; an off-by-one in the truncation makes emitted proofs diverge from the consensus-spec compute_cells_and_kzg_proofs, making the library use inconsistent values for `this client's accept set` and `c-kzg's accept set for the identical bytes` and breaking the invariant that the emitted cells/proofs/commitment equal the consensus-spec and c-kzg output for the same blob and trusted setup? Verify this specifically the second call must produce the identical decision, and assert `this client's accept set` equals `c-kzg's accept set for the identical bytes`.

## Target
- File/function: `internal/kzg_multi/fk20/fk20.go` -> `NewFK20`
- Entrypoint: `Context.ComputeCellsAndKZGProofs` (unprivileged; caller-authored bytes over the normal protocol path)
- Attacker controls: a blob whose proofs expose the srs[evalSetSize:] boundary
- Exploit idea: NewFK20 reverses and truncates the monomial SRS with srs[evalSetSize:] and takeEveryNth; an off-by-one in the truncation makes emitted proofs diverge from the consensus-spec compute_cells_and_kzg_proofs. Construct a blob whose proofs expose the srs[evalSetSize:] boundary and route it through `Context.ComputeCellsAndKZGProofs` into `NewFK20` (internal/kzg_multi/fk20/fk20.go); the second call must produce the identical decision. The witness is the gap between `this client's accept set` and `c-kzg's accept set for the identical bytes`.
- Invariant to test: the emitted cells/proofs/commitment equal the consensus-spec and c-kzg output for the same blob and trusted setup
- Expected Immunefi impact: High - go-eth-kzg and the consensus spec / c-kzg disagree on acceptance or on the emitted commitment, proof, cell, or recovered data for the same input (client split), a valid proof is rejected, or pooled state from one call changes another's result
- Fast validation: Write a `go test` in the root package that constructs the input with the embedded `NewContext4096Secure()` setup (or loads a vector from `tests/`), drives `NewFK20` in `internal/kzg_multi/fk20/fk20.go`, and asserts both sides of the invariant equality before and after the call; mock nothing, use only in-process calls.
