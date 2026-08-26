# Q0805: LogExpMath.ln - an out-of-range operand reverts and blocks the harvest path

## Question
libraries/LogExpMath.sol: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Under the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, is there an unprivileged sequence of `ln(int256 a)` that leaves `maxSwapAmount() in SmartWomConvert` unreconciled with `IAsset cash and liability`, violates the invariant that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: libraries/LogExpMath.sol -> `ln(int256 a)` (mechanism: an out-of-range operand reverts and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `ln(int256 a)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand range reached through the Wombat pricing that SmartWomConvert reads
- Exploit idea: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Precondition: the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee.
- Invariant to test: a pricing routine on the principal path must not be able to revert on attacker-reachable inputs; concretely, `maxSwapAmount() in SmartWomConvert` must stay reconciled with `IAsset cash and liability`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `ln(int256 a)`: constrain the setup so that the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, fuzz the attacker inputs (the operand range reached through the Wombat pricing that SmartWomConvert reads), and assert after every call that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs.
