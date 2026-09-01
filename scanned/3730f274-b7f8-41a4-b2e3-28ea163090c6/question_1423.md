# Q1423: `get_handled_payout_kickoff_txid` and transaction boundaries around on-chain actions

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port time an on-chain event so `get_handled_payout_kickoff_txid` in `core/src/database/verifier.rs` commits part of a state change while the broadcast that must accompany it fails (or the reverse), leaving bookkeeping and the chain permanently inconsistent for a deposit?

## Target
- File/function: `core/src/database/verifier.rs` -> `get_handled_payout_kickoff_txid` (This module includes database functions which are mainly used by a verifier)
- Entrypoint: attacker-timed on-chain events -> `get_handled_payout_kickoff_txid`
- Attacker controls: the timing that makes one half of the operation fail; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: split an atomic protocol step
- Invariant to test: a state change and its on-chain effect are committed atomically or both rolled back
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: inject failures between the two halves and assert consistency
