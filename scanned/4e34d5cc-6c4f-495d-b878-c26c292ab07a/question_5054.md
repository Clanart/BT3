# Q5054: mWOMSVBaseRewarder.getRewards - queued backlog released at an attacker-chosen stake distribution

## Question
rewards/mWOMSVBaseRewarder.sol: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `totalStaked()` unreconciled with `IERC20(mWOMSV).totalSupply()`, violates the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: the attacker settles the same reward token through two separate multiclaimSpec calls in one block.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence atomically under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, asserting at the end that `totalStaked()` still equals `IERC20(mWOMSV).totalSupply()` and the PoC's balance delta is non-positive.
