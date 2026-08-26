# Q5009: vlMGPBaseRewarder.getRewards - InvalidRewardableAmount revert bricks a user's claims

## Question
Note that in rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Can an attacker holding only tokens bought on market reach it via `getRewards(address _account, address _receiver, address[] _rewardTokens)` under the attacker settles the same reward token through two separate multiclaimSpec calls in one block and force `forfeitAmount` apart from `rewardInfo.rewardPerTokenStored`, breaking the invariant that a pricing helper on the claim path must never be able to permanently block settlement for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: the attacker settles the same reward token through two separate multiclaimSpec calls in one block.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, asserting at the end that `forfeitAmount` still equals `rewardInfo.rewardPerTokenStored` and the PoC's balance delta is non-positive.
