# Q0433: LogExpMath.exp - an out-of-range operand reverts and blocks the harvest path

## Question
Consider libraries/LogExpMath.sol, where these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Assuming womCash exceeds womLiability so the swap ceiling collapses to zero, can an unprivileged attacker turn this into a divergence between `the exponent operand` and `the bounds enforced before the call` via `exp(int256 x)`, breaking the invariant that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: libraries/LogExpMath.sol -> `exp(int256 x)` (mechanism: an out-of-range operand reverts and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `exp(int256 x)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction
- Exploit idea: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Precondition: womCash exceeds womLiability so the swap ceiling collapses to zero.
- Invariant to test: a pricing routine on the principal path must not be able to revert on attacker-reachable inputs; concretely, `the exponent operand` must stay reconciled with `the bounds enforced before the call`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up womCash exceeds womLiability so the swap ceiling collapses to zero, snapshot `the exponent operand` and `the bounds enforced before the call`, run the attacker's `exp(int256 x)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
