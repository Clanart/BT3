# Q2151: `get_last_processed_lcp` and index/type width conversions

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port choose an index or amount that changes value crossing the boundary in `get_last_processed_lcp` in `core/src/database/state_machine.rs` (i32/u32/i64 conversion, endianness of a stored outpoint vout, signed comparison) so a lookup returns a different row than the caller intended?

## Target
- File/function: `core/src/database/state_machine.rs` -> `get_last_processed_lcp` (This module includes database functions for persisting and loading state machines)
- Entrypoint: aggregator request or Citrea record with an extreme index -> `get_last_processed_lcp`
- Attacker controls: the index or amount value submitted; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: make a lookup resolve to the wrong protocol record
- Invariant to test: a value stored and read back through `get_last_processed_lcp` is bit-identical for the whole domain
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: round-trip boundary values through `get_last_processed_lcp` and assert equality
