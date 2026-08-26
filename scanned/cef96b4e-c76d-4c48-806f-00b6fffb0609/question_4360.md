# Q4360: MasterMagpie.withdraw - withdraw sends to msg.sender while accounting debits _account

## Question
In rewards/MasterMagpie.sol, _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Can an unprivileged attacker reach this through `withdraw(address _stakingToken, uint256 _amount)` while the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction, and drive `userInfo[_stakingToken][user].rewardDebt` out of agreement with `tokenToPoolInfo[_stakingToken].accMGPPerShare` - breaking the invariant that the address whose UserInfo is debited must be the address that receives the tokens - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: withdraw sends to msg.sender while accounting debits _account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Precondition: the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction.
- Invariant to test: the address whose UserInfo is debited must be the address that receives the tokens; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction, have the attacker run `withdraw(address _stakingToken, uint256 _amount)`, then assert the victim's claimable value and the `userInfo[_stakingToken][user].rewardDebt` versus `tokenToPoolInfo[_stakingToken].accMGPPerShare` relation are unchanged by the attacker's transaction.
