# Q0296: DelegateVoteRewardPool.getReward - getReward is public and settles any account

## Question
Note that in rewards/DelegateVoteRewardPool.sol, getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Can an attacker holding only tokens bought on market reach it via `getReward(address _for)` under the bribe contract for a voted pool registers more than one reward token and force `rewards[_rewardToken].rewardPerTokenStored` apart from `totalSupply of the delegate pool`, breaking the invariant that only the account itself may decide when its rewards are settled for High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: getReward is public and settles any account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Precondition: the bribe contract for a voted pool registers more than one reward token.
- Invariant to test: only the account itself may decide when its rewards are settled; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the bribe contract for a voted pool registers more than one reward token, snapshot `rewards[_rewardToken].rewardPerTokenStored` and `totalSupply of the delegate pool`, run the attacker's `getReward(address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
