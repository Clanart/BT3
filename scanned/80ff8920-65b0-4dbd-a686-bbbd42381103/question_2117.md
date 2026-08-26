# Q2117: DelegateVoteRewardPool.getReward - getReward is public and settles any account

## Question
In rewards/DelegateVoteRewardPool.sol, getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Starting from a state where a bribe token has a transfer hook the attacker controls, can an unprivileged EOA use `getReward(address _for)` to leave `rewards[_rewardToken].rewardPerTokenStored` inconsistent with `totalSupply of the delegate pool`, violating the invariant that only the account itself may decide when its rewards are settled and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: getReward is public and settles any account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Precondition: a bribe token has a transfer hook the attacker controls.
- Invariant to test: only the account itself may decide when its rewards are settled; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up a bribe token has a transfer hook the attacker controls, snapshot `rewards[_rewardToken].rewardPerTokenStored` and `totalSupply of the delegate pool`, run the attacker's `getReward(address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
