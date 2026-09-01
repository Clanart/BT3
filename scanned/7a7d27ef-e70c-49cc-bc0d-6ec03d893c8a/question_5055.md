# Q5055: `run_once` and the provenance of Citrea-sourced records

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction cause `run_once` in `core/src/task/payout_checker.rs` to ingest a deposit or withdrawal record that did not come from the canonical Bridge contract state at the finalized height - a reorged L2 view, a record read at a height later rolled back, or an index read past the end - so the bridge acts on a withdrawal intent that Citrea does not actually hold?

## Target
- File/function: `core/src/task/payout_checker.rs` -> `run_once`
- Entrypoint: a Citrea transaction submitted by an unprivileged Citrea user -> `run_once`
- Attacker controls: the Citrea transactions the attacker submits and their inclusion height; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: get the bridge to honour a withdrawal intent that does not exist in finalized L2 state
- Invariant to test: every withdrawal UTXO and move txid the bridge acts on == the value in finalized Bridge contract storage
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: mock the Citrea client to return a rolled-back record and assert the bridge does not settle
