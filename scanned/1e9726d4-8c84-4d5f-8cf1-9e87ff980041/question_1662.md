# Q1662: `calculate_bump_feerate_if_needed` and inputs added during funding

## Question
Can any unprivileged party who can broadcast a Bitcoin transaction and pay fees supply or influence a UTXO that `calculate_bump_feerate_if_needed` in `crates/clementine-tx-sender/src/rbf.rs` adds while funding a bridge transaction (an attacker-sent payment to the entity's wallet, a UTXO with an unexpected script or an unconfirmed parent), so the funded transaction spends something it should not, becomes unconfirmable, or leaks a bridge-controlled output into the fee?

## Target
- File/function: `crates/clementine-tx-sender/src/rbf.rs` -> `calculate_bump_feerate_if_needed`
- Entrypoint: a Bitcoin payment to the entity's wallet by an unprivileged party paying only mining fees -> `calculate_bump_feerate_if_needed`
- Attacker controls: the UTXOs the attacker donates and their properties; attacker is an unprivileged party who can broadcast Bitcoin transactions, pay fees and send payments to a public address
- Exploit idea: poison the funding set of a bridge transaction
- Invariant to test: the inputs added while funding are wallet UTXOs that are safe to spend and do not include protocol UTXOs
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: donate adversarial UTXOs and assert `calculate_bump_feerate_if_needed` excludes protocol and unsafe inputs
