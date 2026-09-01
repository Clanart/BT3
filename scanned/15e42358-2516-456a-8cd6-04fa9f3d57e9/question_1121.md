# Q1121: `get_payout_tx_blockhash_derivation` and anchor/dust output accounting

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator pick deposit or withdrawal amounts that make `get_payout_tx_blockhash_derivation` in `core/src/bitvm_client.rs` build an output at or below the dust/anchor threshold (`DIGIT_LEN`), so a bridge transaction in the presigned graph is non-standard and can never be broadcast, stranding the vault it protects?

## Target
- File/function: `core/src/bitvm_client.rs` -> `get_payout_tx_blockhash_derivation`
- Entrypoint: aggregator `NewDeposit` -> `get_payout_tx_blockhash_derivation`
- Attacker controls: amounts and fee-related parameters carried in the request; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: make a link of the presigned graph permanently unbroadcastable
- Invariant to test: every transaction in the presigned graph is standard and relayable at construction time
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: build the full graph for boundary amounts and assert every tx passes `testmempoolaccept`
