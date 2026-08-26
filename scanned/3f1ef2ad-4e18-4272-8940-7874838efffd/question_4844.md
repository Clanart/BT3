# Q4844: vlMGPBaseRewarder.getReward - InvalidRewardableAmount revert bricks a user's claims

## Question
Consider rewards/vlMGPBaseRewarder.sol, where _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Assuming a registered reward token has begun reverting on transfer, can an unprivileged attacker turn this into a divergence between `forfeitAmount` and `rewardInfo.rewardPerTokenStored` via `getReward(address _account, address _receiver)`, breaking the invariant that a pricing helper on the claim path must never be able to permanently block settlement and producing Critical - Permanent freezing of funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under a registered reward token has begun reverting on transfer, asserting on every row that a pricing helper on the claim path must never be able to permanently block settlement.
