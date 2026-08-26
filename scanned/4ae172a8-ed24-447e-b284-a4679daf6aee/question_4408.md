# Q4408: WombatPoolHelper.depositLP - safeApprove without reset before depositFor into MasterMagpie

## Question
Note that in wombat/WombatPoolHelper.sol, _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Can an attacker holding only tokens bought on market reach it via `depositLP(uint256 _lpAmount)` under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body and force `_liquidity burned via burnReceiptToken` apart from `the deposit-token balance delta paid out by WombatStaking.withdraw`, breaking the invariant that an approval on the deposit hot path must be idempotent for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `depositLP(uint256 _lpAmount)`: constrain the setup so that the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, fuzz the attacker inputs (_lpAmount and the LP tokens pulled from the caller), and assert after every call that an approval on the deposit hot path must be idempotent.
