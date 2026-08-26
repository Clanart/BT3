# Q4853: MasterMagpie.withdraw - withdraw sends to msg.sender while accounting debits _account

## Question
In rewards/MasterMagpie.sol, _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Does `withdraw(address _stakingToken, uint256 _amount)` let an unprivileged caller exploit that under the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, so that `unClaimedMgp[_stakingToken][user]` diverges from `userInfo[_stakingToken][user].rewardDebt`, the invariant that the address whose UserInfo is debited must be the address that receives the tokens is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: withdraw sends to msg.sender while accounting debits _account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: the address whose UserInfo is debited must be the address that receives the tokens; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `withdraw(address _stakingToken, uint256 _amount)`: constrain the setup so that the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, fuzz the attacker inputs (_stakingToken, _amount, and withdraw ordering inside a block), and assert after every call that the address whose UserInfo is debited must be the address that receives the tokens.
