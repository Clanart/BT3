# Q2967: `mini_assert_derivations_0` and the `bridge_amount` equality

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator reach `mini_assert_derivations_0` in `core/src/bitvm_client.rs` with a deposit output whose value is not exactly `bridge_amount` - via a dust/anchor output, a fee-bumped replacement of the deposit tx, or a value that only matches after `PK_LEN` arithmetic - and still have the move-to-vault presigned, so the vault holds less BTC than the cBTC minted against it?

## Target
- File/function: `core/src/bitvm_client.rs` -> `mini_assert_derivations_0`
- Entrypoint: aggregator `NewDeposit` -> `Verifier::is_deposit_valid` value check -> `mini_assert_derivations_0`
- Attacker controls: the deposit transaction's output value, its RBF replacements, and its confirmation timing; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: back a full-size mint with an under-funded vault
- Invariant to test: the satoshi value locked in the accepted deposit output == `paramset.bridge_amount`
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: regtest: submit a deposit one satoshi short and assert `is_deposit_valid` rejects it
