# Q5670: MasterMagpie.withdraw - withdraw sends to msg.sender while accounting debits _account

## Question
Note that in rewards/MasterMagpie.sol, _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Can an attacker holding only tokens bought on market reach it via `withdraw(address _stakingToken, uint256 _amount)` under the contract is paused so only emergencyWithdraw is reachable and force `IBaseRewardPool(rewarder).balanceOf(user)` apart from `IBaseRewardPool(rewarder).totalStaked()`, breaking the invariant that the address whose UserInfo is debited must be the address that receives the tokens for Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: withdraw sends to msg.sender while accounting debits _account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: the address whose UserInfo is debited must be the address that receives the tokens; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract is paused so only emergencyWithdraw is reachable, then assert `IBaseRewardPool(rewarder).balanceOf(user)` and `IBaseRewardPool(rewarder).totalStaked()` end identical in both runs.
