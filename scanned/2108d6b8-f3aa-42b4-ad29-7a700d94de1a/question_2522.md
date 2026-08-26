# Q2522: mWOMSVBaseRewarder.getReward - InvalidRewardableAmount revert bricks a user's claims

## Question
In rewards/mWOMSVBaseRewarder.sol, _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside mWOMSV from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while the computed forfeit lands just above the _amount / 1000 dust threshold, and drive `forfeitAmount` out of agreement with `rewardInfo.rewardPerTokenStored` - breaking the invariant that a pricing helper on the claim path must never be able to permanently block settlement - for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside mWOMSV from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that the computed forfeit lands just above the _amount / 1000 dust threshold, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that a pricing helper on the claim path must never be able to permanently block settlement.
