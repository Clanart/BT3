# Q0303: `set_block_proof` and index/type width conversions

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port choose an index or amount that changes value crossing the boundary in `set_block_proof` in `core/src/database/header_chain_prover.rs` (i32/u32/i64 conversion, endianness of a stored outpoint vout, signed comparison) so a lookup returns a different row than the caller intended?

## Target
- File/function: `core/src/database/header_chain_prover.rs` -> `set_block_proof` (This module includes database functions which are mainly used by the header)
- Entrypoint: aggregator request or Citrea record with an extreme index -> `set_block_proof`
- Attacker controls: the index or amount value submitted; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: make a lookup resolve to the wrong protocol record
- Invariant to test: a value stored and read back through `set_block_proof` is bit-identical for the whole domain
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: round-trip boundary values through `set_block_proof` and assert equality
