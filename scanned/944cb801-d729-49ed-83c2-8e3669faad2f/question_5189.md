# Q5189: `run_once` and value siphoned into fees or change

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction choose withdrawal-UTXO and output values so that when `run_once` in `core/src/task/lcp_syncer.rs` funds and signs the settlement (`fund_raw_transaction` with `add_inputs`, change position, and the fee rate it derives), more value than the protocol fee is drawn from the funding party or from the vault into fees or an attacker-influenced change output?

## Target
- File/function: `core/src/task/lcp_syncer.rs` -> `run_once`
- Entrypoint: aggregator `Withdraw` -> `run_once`
- Attacker controls: the withdrawal UTXO value, the requested output amount, and the mempool conditions that set the fee rate; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: extract value beyond the intended spread from the party funding the payout
- Invariant to test: value drawn from the funder == requested output value minus the withdrawal input value, plus the bounded fee
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: assert the funded settlement's fee and change against a value-conservation check for adversarial amounts
