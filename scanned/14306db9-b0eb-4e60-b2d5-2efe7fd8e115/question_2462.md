# Q2462: `add_tx_to_queue` and witness/signature carry-over

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees shape a transaction so `add_tx_to_queue` in `core/src/tx_sender_queue.rs` copies a witness onto an input it does not belong to, or reorders inputs/outputs after signing, so a valid bridge signature is applied to a different spend than intended?

## Target
- File/function: `core/src/tx_sender_queue.rs` -> `add_tx_to_queue` (Core-specific txsender queue helpers)
- Entrypoint: attacker-influenced funding conditions -> `add_tx_to_queue`
- Attacker controls: the number and ordering of inputs and outputs during funding; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: misapply a bridge signature across inputs
- Invariant to test: each witness ends on the input whose sighash it was produced for
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: assert input/output ordering is stable across the sign-fund-finalize path
