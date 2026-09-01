# Q2994: `get_entity_statuses` and an honest participant made slashable

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction, using only transactions they can broadcast, put the chain into a state where `get_entity_statuses` in `core/src/aggregator.rs` classifies an honest participant's on-chain action as deviating (mismatched committed value, missing acknowledgement, absent payout record), so that participant's collateral is burned even though it followed the protocol?

## Target
- File/function: `core/src/aggregator.rs` -> `get_entity_statuses`
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `get_entity_statuses`
- Attacker controls: the shape and timing of the transactions the attacker places on chain; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: trigger slashing of a participant that did nothing wrong
- Invariant to test: a participant is classified as deviating only when its own on-chain actions actually diverged from the protocol
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: regtest: construct the adversarial chain state and assert `get_entity_statuses` does not classify the honest party as malicious
