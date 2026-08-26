# Q0526: LogExpMath.ln - an out-of-range operand reverts and blocks the harvest path

## Question
In libraries/LogExpMath.sol, these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Can an unprivileged attacker reach this through `ln(int256 a)` while womCash exceeds womLiability so the swap ceiling collapses to zero, and drive `currentRatio() in SmartWomConvert` out of agreement with `the value returned by the underlying math` - breaking the invariant that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: libraries/LogExpMath.sol -> `ln(int256 a)` (mechanism: an out-of-range operand reverts and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `ln(int256 a)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand range reached through the Wombat pricing that SmartWomConvert reads
- Exploit idea: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Precondition: womCash exceeds womLiability so the swap ceiling collapses to zero.
- Invariant to test: a pricing routine on the principal path must not be able to revert on attacker-reachable inputs; concretely, `currentRatio() in SmartWomConvert` must stay reconciled with `the value returned by the underlying math`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `ln(int256 a)`: constrain the setup so that womCash exceeds womLiability so the swap ceiling collapses to zero, fuzz the attacker inputs (the operand range reached through the Wombat pricing that SmartWomConvert reads), and assert after every call that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs.
