# Q1946: vlMGPBaseRewarder.getReward - InvalidRewardableAmount revert bricks a user's claims

## Question
In rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while the computed forfeit lands just below the _amount / 1000 dust threshold, and drive `balanceOf(account)` out of agreement with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` - breaking the invariant that a pricing helper on the claim path must never be able to permanently block settlement - for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under the computed forfeit lands just below the _amount / 1000 dust threshold, asserting on every row that a pricing helper on the claim path must never be able to permanently block settlement.
