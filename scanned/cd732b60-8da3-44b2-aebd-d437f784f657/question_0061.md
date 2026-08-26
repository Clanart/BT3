# Q0061: LogExpMath.pow - an out-of-range operand reverts and blocks the harvest path

## Question
In libraries/LogExpMath.sol, these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Does `pow(uint256 x, uint256 y)` let an unprivileged caller exploit that under the attacker has pushed the wom/mWom pool far off peg in the same transaction, so that `currentRatio() in SmartWomConvert` diverges from `the value returned by the underlying math`, the invariant that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: libraries/LogExpMath.sol -> `pow(uint256 x, uint256 y)` (mechanism: an out-of-range operand reverts and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `pow(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert
- Exploit idea: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Precondition: the attacker has pushed the wom/mWom pool far off peg in the same transaction.
- Invariant to test: a pricing routine on the principal path must not be able to revert on attacker-reachable inputs; concretely, `currentRatio() in SmartWomConvert` must stay reconciled with `the value returned by the underlying math`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has pushed the wom/mWom pool far off peg in the same transaction, have the attacker run `pow(uint256 x, uint256 y)`, then assert the victim's claimable value and the `currentRatio() in SmartWomConvert` versus `the value returned by the underlying math` relation are unchanged by the attacker's transaction.
