# Q2902: vlMGPBaseRewarder.getRewards - queued backlog released at an attacker-chosen stake distribution

## Question
rewards/vlMGPBaseRewarder.sol: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Under a large MGP distribution has just been queued and no account has settled yet, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `totalStaked()` unreconciled with `IERC20(vlMGP).totalSupply()`, violates the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block locker, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: queued backlog released at an attacker-chosen stake distribution)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: when totalStaked() is zero the provision accumulates into queuedRewards and is released in full on the next provision, so an attacker who locks at that exact moment absorbs the whole backlog. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block locker; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `getRewards(address _account, address _receiver, address[] _rewardTokens)`: constrain the setup so that a large MGP distribution has just been queued and no account has settled yet, fuzz the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor), and assert after every call that a backlog accrued while the pool was empty must not be assignable to a single one-block locker.
