# Q2344: AnkrBNBPoolHelper.withdraw - the lockedAmount restriction is enforced only inside this helper

## Question
wombat/AnkrBNBPoolHelper.sol: withdraw() checks unlockTime and lockedAmount[msg.sender] against this.balance(msg.sender) after unstaking, but the underlying position is ordinary MasterMagpie stake in the same stakingToken, so the restriction is only as strong as this one code path. Under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, is there an unprivileged sequence of `withdraw(uint256 _liquidity, uint256 _minAmount)` that leaves `IERC20(stakingToken).balanceOf(address(this)) delta` unreconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, violates the invariant that a time restriction on a position must be enforced where the position lives, not in one optional front-end contract, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: the lockedAmount restriction is enforced only inside this helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: withdraw() checks unlockTime and lockedAmount[msg.sender] against this.balance(msg.sender) after unstaking, but the underlying position is ordinary MasterMagpie stake in the same stakingToken, so the restriction is only as strong as this one code path. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: a time restriction on a position must be enforced where the position lives, not in one optional front-end contract; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity, _minAmount and the ordering against the lockedAmount check) under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, asserting on every row that a time restriction on a position must be enforced where the position lives, not in one optional front-end contract.
