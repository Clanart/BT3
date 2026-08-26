# Q4222: mWOMSVBaseRewarder.getReward - InvalidRewardableAmount revert bricks a user's claims

## Question
rewards/mWOMSVBaseRewarder.sol - _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside mWOMSV from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Can an unprivileged attacker controlling the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path, under the attacker locks one block before a known large settlement and unlocks one block after, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `totalStaked()` and `IERC20(mWOMSV).totalSupply()` and the invariant that a pricing helper on the claim path must never be able to permanently block settlement, yielding Critical - Permanent freezing of funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside mWOMSV from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker locks one block before a known large settlement and unlocks one block after, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `totalStaked()` versus `IERC20(mWOMSV).totalSupply()` relation are unchanged by the attacker's transaction.
