# Q0569: `deposit_finalize` and the `bridge_amount` equality

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator reach `deposit_finalize` in `core/src/verifier.rs` with a deposit output whose value is not exactly `bridge_amount` - via a dust/anchor output, a fee-bumped replacement of the deposit tx, or a value that only matches after `LCP_SYNCER_CONSUMER_ID` arithmetic - and still have the move-to-vault presigned, so the vault holds less BTC than the cBTC minted against it?

## Target
- File/function: `core/src/verifier.rs` -> `deposit_finalize`
- Entrypoint: aggregator `NewDeposit` -> `Verifier::is_deposit_valid` value check -> `deposit_finalize`
- Attacker controls: the deposit transaction's output value, its RBF replacements, and its confirmation timing; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: back a full-size mint with an under-funded vault
- Invariant to test: the satoshi value locked in the accepted deposit output == `paramset.bridge_amount`
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: regtest: submit a deposit one satoshi short and assert `is_deposit_valid` rejects it
