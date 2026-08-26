# Q5766: MasterMagpie.withdraw - withdraw sends to msg.sender while accounting debits _account

## Question
Note that in rewards/MasterMagpie.sol, _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Can an attacker holding only tokens bought on market reach it via `withdraw(address _stakingToken, uint256 _amount)` under the victim has a large unClaimedMgp balance that has not been settled for several epochs and force `totalAllocPoint` apart from `tokenToPoolInfo[_stakingToken].allocPoint`, breaking the invariant that the address whose UserInfo is debited must be the address that receives the tokens for Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: withdraw sends to msg.sender while accounting debits _account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: the address whose UserInfo is debited must be the address that receives the tokens; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `withdraw(address _stakingToken, uint256 _amount)`: constrain the setup so that the victim has a large unClaimedMgp balance that has not been settled for several epochs, fuzz the attacker inputs (_stakingToken, _amount, and withdraw ordering inside a block), and assert after every call that the address whose UserInfo is debited must be the address that receives the tokens.
