# Q4390: vlMGPBaseRewarder.getRewards - dust threshold waives the forfeit entirely

## Question
rewards/vlMGPBaseRewarder.sol: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Under the victim has not settled for several epochs and holds a large userRewards balance, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `forfeitAmount` unreconciled with `rewardInfo.rewardPerTokenStored`, violates the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getRewards(address _account, address _receiver, address[] _rewardTokens)`: constrain the setup so that the victim has not settled for several epochs and holds a large userRewards balance, fuzz the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor), and assert after every call that a rounding convenience must not create a settlement size at which the forfeit rule stops applying.
