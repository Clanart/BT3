# Q0712: LogExpMath.exp - an out-of-range operand reverts and blocks the harvest path

## Question
libraries/LogExpMath.sol: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. With the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction under attacker control and the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, can an unprivileged caller sequence `exp(int256 x)` so that `currentRatio() in SmartWomConvert` and `the value returned by the underlying math` no longer reconcile, violating the invariant that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: libraries/LogExpMath.sol -> `exp(int256 x)` (mechanism: an out-of-range operand reverts and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `exp(int256 x)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction
- Exploit idea: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Precondition: the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee.
- Invariant to test: a pricing routine on the principal path must not be able to revert on attacker-reachable inputs; concretely, `currentRatio() in SmartWomConvert` must stay reconciled with `the value returned by the underlying math`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, snapshot `currentRatio() in SmartWomConvert` and `the value returned by the underlying math`, run the attacker's `exp(int256 x)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
