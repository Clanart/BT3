# Q1015: `get_block_proof_by_hash` and 'first'/'next' selection

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port influence which row `get_block_proof_by_hash` in `core/src/database/header_chain_prover.rs` selects as the first or next item (unhandled payout, unused connector, next round) by controlling insertion order or by inserting a colliding row, so the protocol acts on an item of the attacker's choosing?

## Target
- File/function: `core/src/database/header_chain_prover.rs` -> `get_block_proof_by_hash` (This module includes database functions which are mainly used by the header)
- Entrypoint: attacker-timed on-chain or Citrea events -> `get_block_proof_by_hash`
- Attacker controls: the insertion order and content of competing rows; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: steer the protocol onto an item the attacker selected
- Invariant to test: the selection `get_block_proof_by_hash` makes is a deterministic function of protocol state, not of insertion timing
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: insert competing rows adversarially and assert selection is canonical
