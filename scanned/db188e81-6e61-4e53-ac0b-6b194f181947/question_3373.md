# Q3373: vlMGPBaseRewarder.getRewards - queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool

## Question
Consider rewards/vlMGPBaseRewarder.sol, where this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Assuming the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that only an authorised manager may decide when and by how much the reward index moves and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getRewards(address _account, address _receiver, address[] _rewardTokens)`: constrain the setup so that the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, fuzz the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor), and assert after every call that only an authorised manager may decide when and by how much the reward index moves.
