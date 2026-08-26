# Q1714: mWOMSVBaseRewarder.getRewards - InvalidRewardableAmount revert bricks a user's claims

## Question
rewards/mWOMSVBaseRewarder.sol: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside mWOMSV from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Under the computed forfeit lands just below the _amount / 1000 dust threshold, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `totalStaked()` unreconciled with `IERC20(mWOMSV).totalSupply()`, violates the invariant that a pricing helper on the claim path must never be able to permanently block settlement, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside mWOMSV from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the computed forfeit lands just below the _amount / 1000 dust threshold, have the attacker run `getRewards(address _account, address _receiver, address[] _rewardTokens)`, then assert the victim's claimable value and the `totalStaked()` versus `IERC20(mWOMSV).totalSupply()` relation are unchanged by the attacker's transaction.
