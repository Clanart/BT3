# Q5053: vlMGPBaseRewarder.getRewards - queued backlog released at an attacker-chosen stake distribution

## Question
Note that in rewards/vlMGPBaseRewarder.sol, when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Can an attacker holding only tokens bought on market reach it via `getRewards(address _account, address _receiver, address[] _rewardTokens)` under the attacker settles the same reward token through two separate multiclaimSpec calls in one block and force `totalStaked()` apart from `IERC20(vlMGP).totalSupply()`, breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker for Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: the attacker settles the same reward token through two separate multiclaimSpec calls in one block.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, then assert `totalStaked()` and `IERC20(vlMGP).totalSupply()` end identical in both runs.
