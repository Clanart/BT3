# Q1323: vlMGPBaseRewarder.getReward - InvalidRewardableAmount revert bricks a user's claims

## Question
rewards/vlMGPBaseRewarder.sol: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. With the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path under attacker control and the account's slot matured recently so the percent has only just begun to decay, can an unprivileged caller sequence `getReward(address _account, address _receiver)` so that `totalStaked()` and `IERC20(vlMGP).totalSupply()` no longer reconcile, violating the invariant that a pricing helper on the claim path must never be able to permanently block settlement and realising Critical - Permanent freezing of funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the account's slot matured recently so the percent has only just begun to decay, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `totalStaked()` versus `IERC20(vlMGP).totalSupply()` relation are unchanged by the attacker's transaction.
