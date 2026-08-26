# Q0340: LogExpMath.pow - an out-of-range operand reverts and blocks the harvest path

## Question
libraries/LogExpMath.sol - these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Can an unprivileged attacker controlling the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert, under womCash exceeds womLiability so the swap ceiling collapses to zero, exploit this through `pow(uint256 x, uint256 y)` to break the reconciliation between `maxSwapAmount() in SmartWomConvert` and `IAsset cash and liability` and the invariant that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: libraries/LogExpMath.sol -> `pow(uint256 x, uint256 y)` (mechanism: an out-of-range operand reverts and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `pow(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert
- Exploit idea: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Precondition: womCash exceeds womLiability so the swap ceiling collapses to zero.
- Invariant to test: a pricing routine on the principal path must not be able to revert on attacker-reachable inputs; concretely, `maxSwapAmount() in SmartWomConvert` must stay reconciled with `IAsset cash and liability`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish womCash exceeds womLiability so the swap ceiling collapses to zero, have the attacker run `pow(uint256 x, uint256 y)`, then assert the victim's claimable value and the `maxSwapAmount() in SmartWomConvert` versus `IAsset cash and liability` relation are unchanged by the attacker's transaction.
