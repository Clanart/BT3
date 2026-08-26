# Q0130: vlMGPBaseRewarder.updateFor - queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool

## Question
Note that in rewards/vlMGPBaseRewarder.sol, this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18 and force `userRewards[_rewardToken][account]` apart from `rewards[_rewardToken].rewardPerTokenStored`, breaking the invariant that only an authorised manager may decide when and by how much the reward index moves for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that only an authorised manager may decide when and by how much the reward index moves.
