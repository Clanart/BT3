# Q1875: `validate_all_kickoff_finalizers_spent` and the light-client proof bound to a payout

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction make `validate_all_kickoff_finalizers_spent` in `core/src/operator.rs` fetch, validate or store a light client proof for an L1 height or deposit index other than the one the settlement will be proved against - an off-by-one height, a stale cached entry, or an index reused across deposits - so the proof later presented does not correspond to the payout it settles?

## Target
- File/function: `core/src/operator.rs` -> `validate_all_kickoff_finalizers_spent`
- Entrypoint: aggregator `Withdraw` then attacker-controlled L1/L2 timing -> `validate_all_kickoff_finalizers_spent`
- Attacker controls: the block height at which the payout lands and the L2 height range it spans; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: bind a settlement to state from a different height than the one that authorises it
- Invariant to test: the L1 height of the stored light client proof == the height of the block containing the payout being settled
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: assert the stored proof's height equals the payout block height for adversarial timings
