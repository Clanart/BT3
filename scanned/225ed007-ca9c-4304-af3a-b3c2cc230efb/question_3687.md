# Q3687: mWOMSVBaseRewarder.getRewards - InvalidRewardableAmount revert bricks a user's claims

## Question
rewards/mWOMSVBaseRewarder.sol - _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside mWOMSV from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Can an unprivileged attacker controlling the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor, under totalStaked is zero and queuedRewards holds a backlog, exploit this through `getRewards(address _account, address _receiver, address[] _rewardTokens)` to break the reconciliation between `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)` and the invariant that a pricing helper on the claim path must never be able to permanently block settlement, yielding Critical - Permanent freezing of funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside mWOMSV from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange totalStaked is zero and queuedRewards holds a backlog, call `getRewards(address _account, address _receiver, address[] _rewardTokens)`, and assert `_calExpireForfeit(account,_amount)` equals `mWOMSV.getRewardablePercentWAD(account)` and that no account can withdraw more than it put in.
