# Q3962: AnkrBNBPoolHelper.withdraw - the lockedAmount restriction is enforced only inside this helper

## Question
wombat/AnkrBNBPoolHelper.sol: withdraw() checks unlockTime and lockedAmount[msg.sender] against this.balance(msg.sender) after unstaking, but the underlying position is ordinary MasterMagpie stake in the same stakingToken, so the restriction is only as strong as this one code path. With _liquidity, _minAmount and the ordering against the lockedAmount check under attacker control and the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, can an unprivileged caller sequence `withdraw(uint256 _liquidity, uint256 _minAmount)` so that `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` no longer reconcile, violating the invariant that a time restriction on a position must be enforced where the position lives, not in one optional front-end contract and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: the lockedAmount restriction is enforced only inside this helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: withdraw() checks unlockTime and lockedAmount[msg.sender] against this.balance(msg.sender) after unstaking, but the underlying position is ordinary MasterMagpie stake in the same stakingToken, so the restriction is only as strong as this one code path. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: a time restriction on a position must be enforced where the position lives, not in one optional front-end contract; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, then assert `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` end identical in both runs.
