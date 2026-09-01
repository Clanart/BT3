# Q3010: `vec_to_array` and operator-set drift between deposits

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator submit a `DepositData` whose operator set omits an operator whose collateral is still live, or includes one whose collateral is spent, in a way that slips past the `collateral_check` loop reached from `vec_to_array` in `core/src/bitvm_client.rs` (timing the collateral spend against the check), so the resulting vault has no operator able to serve its withdrawals?

## Target
- File/function: `core/src/bitvm_client.rs` -> `vec_to_array`
- Entrypoint: aggregator `NewDeposit` actor lists -> `vec_to_array`
- Attacker controls: the operator list in `DepositParams` and the timing of the round relative to on-chain collateral movement; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: create a vault that no operator can ever front a payout for
- Invariant to test: the operator set bound into a deposit == the set of operators with usable collateral at settlement time
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: mock `collateral_check` to flip mid-round and assert the deposit is rejected
