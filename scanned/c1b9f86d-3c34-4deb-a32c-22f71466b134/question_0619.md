# Q0619: LogExpMath.pow - an out-of-range operand reverts and blocks the harvest path

## Question
In libraries/LogExpMath.sol, these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Starting from a state where the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, can an unprivileged EOA use `pow(uint256 x, uint256 y)` to leave `the exponent operand` inconsistent with `the bounds enforced before the call`, violating the invariant that a pricing routine on the principal path must not be able to revert on attacker-reachable inputs and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: libraries/LogExpMath.sol -> `pow(uint256 x, uint256 y)` (mechanism: an out-of-range operand reverts and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `pow(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert
- Exploit idea: these routines revert on operands outside their supported domain, and because SmartWomConvert sits inside WombatStaking._sendRewards, such a revert propagates to every deposit, depositLP and withdraw for the pool. Precondition: the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee.
- Invariant to test: a pricing routine on the principal path must not be able to revert on attacker-reachable inputs; concretely, `the exponent operand` must stay reconciled with `the bounds enforced before the call`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, have the attacker run `pow(uint256 x, uint256 y)`, then assert the victim's claimable value and the `the exponent operand` versus `the bounds enforced before the call` relation are unchanged by the attacker's transaction.
