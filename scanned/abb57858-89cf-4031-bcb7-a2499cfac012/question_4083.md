# Q4083: `sign_with_tweak_data` and which UTXOs the sender may sign for

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees cause `sign_with_tweak_data` in `crates/clementine-tx-sender/src/signer.rs` to sign for an outpoint outside the set the tx-sender is meant to control - a protocol UTXO, a collateral outpoint, or another deposit's connector - so a bridge-controlled UTXO is spent by a routine fee or delivery path?

## Target
- File/function: `crates/clementine-tx-sender/src/signer.rs` -> `sign_with_tweak_data`
- Entrypoint: attacker-shaped transaction requests or wallet state -> `sign_with_tweak_data`
- Attacker controls: the outpoints presented for signing and the wallet's contents; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: get the delivery layer to spend a protocol UTXO
- Invariant to test: the outpoints the sender signs for are disjoint from the presigned protocol graph
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: present protocol outpoints to `sign_with_tweak_data` and assert it refuses
