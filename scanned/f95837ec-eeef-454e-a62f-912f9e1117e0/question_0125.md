# Q0125: MasterMagpie.withdraw - withdraw sends to msg.sender while accounting debits _account

## Question
In rewards/MasterMagpie.sol, _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Does `withdraw(address _stakingToken, uint256 _amount)` let an unprivileged caller exploit that under the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, so that `totalAllocPoint` diverges from `tokenToPoolInfo[_stakingToken].allocPoint`, the invariant that the address whose UserInfo is debited must be the address that receives the tokens is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: withdraw sends to msg.sender while accounting debits _account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Precondition: the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it.
- Invariant to test: the address whose UserInfo is debited must be the address that receives the tokens; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, snapshot `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint`, run the attacker's `withdraw(address _stakingToken, uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
