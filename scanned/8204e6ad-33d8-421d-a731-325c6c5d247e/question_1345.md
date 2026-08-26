# Q1345: MasterMagpie.withdraw - withdraw sends to msg.sender while accounting debits _account

## Question
rewards/MasterMagpie.sol: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. With _stakingToken, _amount, and withdraw ordering inside a block under attacker control and the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, can an unprivileged caller sequence `withdraw(address _stakingToken, uint256 _amount)` so that `vlmgp.totalSupply()` and `sum of userInfo[vlmgp][*].amount` no longer reconcile, violating the invariant that the address whose UserInfo is debited must be the address that receives the tokens and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: withdraw sends to msg.sender while accounting debits _account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: the address whose UserInfo is debited must be the address that receives the tokens; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, then assert `vlmgp.totalSupply()` and `sum of userInfo[vlmgp][*].amount` end identical in both runs.
