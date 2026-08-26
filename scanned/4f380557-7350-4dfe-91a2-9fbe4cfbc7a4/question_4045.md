# Q4045: vlMGPBaseRewarder.getRewards - dust threshold waives the forfeit entirely

## Question
Note that in rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Can an attacker holding only tokens bought on market reach it via `getRewards(address _account, address _receiver, address[] _rewardTokens)` under the attacker locks one block before a known large settlement and unlocks one block after and force `balanceOf(account)` apart from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, breaking the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor) under the attacker locks one block before a known large settlement and unlocks one block after, asserting on every row that a rounding convenience must not create a settlement size at which the forfeit rule stops applying.
