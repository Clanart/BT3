# Q3113: MasterMagpie.withdraw - withdraw sends to msg.sender while accounting debits _account

## Question
rewards/MasterMagpie.sol: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. With _stakingToken, _amount, and withdraw ordering inside a block under attacker control and a large honest deposit is sitting in the mempool and the attacker sandwiches it, can an unprivileged caller sequence `withdraw(address _stakingToken, uint256 _amount)` so that `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)` no longer reconcile, violating the invariant that the address whose UserInfo is debited must be the address that receives the tokens and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: withdraw sends to msg.sender while accounting debits _account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: the address whose UserInfo is debited must be the address that receives the tokens; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `withdraw(address _stakingToken, uint256 _amount)`: constrain the setup so that a large honest deposit is sitting in the mempool and the attacker sandwiches it, fuzz the attacker inputs (_stakingToken, _amount, and withdraw ordering inside a block), and assert after every call that the address whose UserInfo is debited must be the address that receives the tokens.
