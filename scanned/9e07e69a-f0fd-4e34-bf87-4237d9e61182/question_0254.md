# Q0254: vlMGPBaseRewarder.getRewards - InvalidRewardableAmount revert bricks a user's claims

## Question
In rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Starting from a state where the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `_calExpireForfeit(account,_amount)` inconsistent with `vlMGP.getRewardablePercentWAD(account)`, violating the invariant that a pricing helper on the claim path must never be able to permanently block settlement and extracting Critical - Permanent freezing of funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, asserting on every row that a pricing helper on the claim path must never be able to permanently block settlement.
