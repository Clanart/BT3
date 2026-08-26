# Q5926: MasterMagpie.withdraw - withdraw sends to msg.sender while accounting debits _account

## Question
Note that in rewards/MasterMagpie.sol, _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Can an attacker holding only tokens bought on market reach it via `withdraw(address _stakingToken, uint256 _amount)` under the attacker repeats the call in the same block to observe the second, no-op iteration and force `mgpPerSec` apart from `IERC20(mgp).balanceOf(masterMagpie)`, breaking the invariant that the address whose UserInfo is debited must be the address that receives the tokens for Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: withdraw sends to msg.sender while accounting debits _account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: the address whose UserInfo is debited must be the address that receives the tokens; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker repeats the call in the same block to observe the second, no-op iteration, then assert `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)` end identical in both runs.
