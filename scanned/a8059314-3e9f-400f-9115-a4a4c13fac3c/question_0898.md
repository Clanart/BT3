# Q0898: LogExpMath.pow - an out-of-range operand reverts and blocks the harvest path

## Question
Consider libraries/LogExpMath.sol, where these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Assuming the attacker routes many small conversions rather than one large one, can an unprivileged attacker turn this into a divergence between `currentRatio() in SmartWomConvert` and `the value returned by the underlying math` via `pow(uint256 x, uint256 y)`, breaking the invariant that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: libraries/LogExpMath.sol -> `pow(uint256 x, uint256 y)` (mechanism: an out-of-range operand reverts and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `pow(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert
- Exploit idea: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Precondition: the attacker routes many small conversions rather than one large one.
- Invariant to test: a pricing routine on the principal path must not be able to revert on attacker-reachable inputs; concretely, `currentRatio() in SmartWomConvert` must stay reconciled with `the value returned by the underlying math`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the attacker routes many small conversions rather than one large one, have the attacker run `pow(uint256 x, uint256 y)`, then assert the victim's claimable value and the `currentRatio() in SmartWomConvert` versus `the value returned by the underlying math` relation are unchanged by the attacker's transaction.
