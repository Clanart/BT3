# Q3622: vlMGPBaseRewarder.updateFor - queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool

## Question
Consider rewards/vlMGPBaseRewarder.sol, where this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Assuming totalStaked is zero and queuedRewards holds a backlog, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` via `updateFor(address _account)`, breaking the invariant that only an authorised manager may decide when and by how much the reward index moves and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up totalStaked is zero and queuedRewards holds a backlog, snapshot `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
