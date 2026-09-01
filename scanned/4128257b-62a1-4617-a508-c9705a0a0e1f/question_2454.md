# Q2454: `debug_tx` and witness/signature carry-over

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees shape a transaction so `debug_tx` in `core/src/tx_sender_ext.rs` copies a witness onto an input it does not belong to, or reorders inputs/outputs after signing, so a valid bridge signature is applied to a different spend than intended?

## Target
- File/function: `core/src/tx_sender_ext.rs` -> `debug_tx`
- Entrypoint: attacker-influenced funding conditions -> `debug_tx`
- Attacker controls: the number and ordering of inputs and outputs during funding; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: misapply a bridge signature across inputs
- Invariant to test: each witness ends on the input whose sighash it was produced for
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: assert input/output ordering is stable across the sign-fund-finalize path
