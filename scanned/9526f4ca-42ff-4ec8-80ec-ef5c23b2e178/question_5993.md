# Q5993: MasterMagpie.withdraw - withdraw sends to msg.sender while accounting debits _account

## Question
rewards/MasterMagpie.sol: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. With _stakingToken, _amount, and withdraw ordering inside a block under attacker control and the attacker splits the action across two transactions in the same block with a flash-loaned staking token, can an unprivileged caller sequence `withdraw(address _stakingToken, uint256 _amount)` so that `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)` no longer reconcile, violating the invariant that the address whose UserInfo is debited must be the address that receives the tokens and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: withdraw sends to msg.sender while accounting debits _account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Precondition: the attacker splits the action across two transactions in the same block with a flash-loaned staking token.
- Invariant to test: the address whose UserInfo is debited must be the address that receives the tokens; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker splits the action across two transactions in the same block with a flash-loaned staking token, call `withdraw(address _stakingToken, uint256 _amount)`, and assert `userInfo[_stakingToken][user].amount` equals `_calLpSupply(_stakingToken)` and that no account can withdraw more than it put in.
