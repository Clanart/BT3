# Q3374: mWOMSVBaseRewarder.getRewards - donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens

## Question
In rewards/mWOMSVBaseRewarder.sol, this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Starting from a state where the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `userRewards[_rewardToken][account]` inconsistent with `rewards[_rewardToken].rewardPerTokenStored`, violating the invariant that only an authorised manager may decide when and by how much the reward index moves and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, then assert `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` end identical in both runs.
