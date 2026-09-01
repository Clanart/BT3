# Q1078: `with_additional_taproot_output_count` and a deadline-bound bridge transaction that cannot be bumped

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees attach a low-fee descendant to, or otherwise constrain, a bridge transaction handled by `with_additional_taproot_output_count` in `crates/tx-sender-types/src/clementine.rs` so the fee-bumping path can no longer raise its effective fee before a protocol deadline, causing the honest branch to lose to a timeout and bridged BTC to be claimed by the other side?

## Target
- File/function: `crates/tx-sender-types/src/clementine.rs` -> `with_additional_taproot_output_count` (Clementine-specific tx-sender types)
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `with_additional_taproot_output_count`
- Attacker controls: descendant transactions, package size and fee rates in the mempool; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: prevent a deadline-bound bridge transaction from confirming in time
- Invariant to test: every deadline-bound bridge transaction remains fee-bumpable to confirmation before its timeout matures
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: regtest: pin the transaction and assert `with_additional_taproot_output_count` still gets it confirmed within the window
