# Q5214: MasterMagpie.withdraw - withdraw sends to msg.sender while accounting debits _account

## Question
Note that in rewards/MasterMagpie.sol, _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Can an attacker holding only tokens bought on market reach it via `withdraw(address _stakingToken, uint256 _amount)` under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked() and force `_calLpSupply(_stakingToken)` apart from `IERC20(_stakingToken).balanceOf(masterMagpie)`, breaking the invariant that the address whose UserInfo is debited must be the address that receives the tokens for Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: withdraw sends to msg.sender while accounting debits _account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: _withdraw() debits userInfo[_stakingToken][_account] but transfers the staking token to msg.sender, so any path where _account and msg.sender differ moves another account's principal to the caller. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: the address whose UserInfo is debited must be the address that receives the tokens; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), snapshot `_calLpSupply(_stakingToken)` and `IERC20(_stakingToken).balanceOf(masterMagpie)`, run the attacker's `withdraw(address _stakingToken, uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
