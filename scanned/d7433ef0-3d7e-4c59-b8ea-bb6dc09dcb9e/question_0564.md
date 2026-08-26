# Q0564: vlMGPBaseRewarder.getReward - InvalidRewardableAmount revert bricks a user's claims

## Question
Consider rewards/vlMGPBaseRewarder.sol, where _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Assuming the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` via `getReward(address _account, address _receiver)`, breaking the invariant that a pricing helper on the claim path must never be able to permanently block settlement and producing Critical - Permanent freezing of funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, call `getReward(address _account, address _receiver)`, and assert `userRewards[_rewardToken][account]` equals `rewards[_rewardToken].rewardPerTokenStored` and that no account can withdraw more than it put in.
