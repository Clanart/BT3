# Q1935: `update_payout_txs_and_payer_operator_xonly_pk` and 'first'/'next' selection

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port influence which row `update_payout_txs_and_payer_operator_xonly_pk` in `core/src/database/verifier.rs` selects as the first or next item (unhandled payout, unused connector, next round) by controlling insertion order or by inserting a colliding row, so the protocol acts on an item of the attacker's choosing?

## Target
- File/function: `core/src/database/verifier.rs` -> `update_payout_txs_and_payer_operator_xonly_pk` (This module includes database functions which are mainly used by a verifier)
- Entrypoint: attacker-timed on-chain or Citrea events -> `update_payout_txs_and_payer_operator_xonly_pk`
- Attacker controls: the insertion order and content of competing rows; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: steer the protocol onto an item the attacker selected
- Invariant to test: the selection `update_payout_txs_and_payer_operator_xonly_pk` makes is a deterministic function of protocol state, not of insertion timing
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: insert competing rows adversarially and assert selection is canonical
