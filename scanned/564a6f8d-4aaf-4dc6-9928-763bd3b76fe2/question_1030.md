# Q1030: mWOMSVBaseRewarder.getRewards - InvalidRewardableAmount revert bricks a user's claims

## Question
In rewards/mWOMSVBaseRewarder.sol, _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside mWOMSV from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Starting from a state where the account's slot matured recently so the percent has only just begun to decay, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `userRewards[_rewardToken][account]` inconsistent with `rewards[_rewardToken].rewardPerTokenStored`, violating the invariant that a pricing helper on the claim path must never be able to permanently block settlement and extracting Critical - Permanent freezing of funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside mWOMSV from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under the account's slot matured recently so the percent has only just begun to decay, asserting on every row that a pricing helper on the claim path must never be able to permanently block settlement.
