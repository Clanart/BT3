# Q2309: MasterMagpie.withdraw - withdraw sends to msg.sender while accounting debits _account

## Question
In rewards/MasterMagpie.sol, _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Does `withdraw(address _stakingToken, uint256 _amount)` let an unprivileged caller exploit that under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, so that `mgpPerSec` diverges from `IERC20(mgp).balanceOf(masterMagpie)`, the invariant that the address whose UserInfo is debited must be the address that receives the tokens is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: withdraw sends to msg.sender while accounting debits _account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: the address whose UserInfo is debited must be the address that receives the tokens; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `withdraw(address _stakingToken, uint256 _amount)`: constrain the setup so that the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, fuzz the attacker inputs (_stakingToken, _amount, and withdraw ordering inside a block), and assert after every call that the address whose UserInfo is debited must be the address that receives the tokens.
