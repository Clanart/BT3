# Q0788: AnkrBNBPoolHelper.withdraw - the lockedAmount restriction is enforced only inside this helper

## Question
In wombat/AnkrBNBPoolHelper.sol, withdraw() checks unlockTime and lockedAmount[msg.sender] against this.balance(msg.sender) after unstaking, but the underlying position is ordinary MasterMagpie stake in the same stakingToken, so the restriction is only as strong as this one code path. Starting from a state where the pool's deposit token is wBNB and the caller arrived through depositNative, can an unprivileged EOA use `withdraw(uint256 _liquidity, uint256 _minAmount)` to leave `_minimumLiquidity supplied by the caller` inconsistent with `the LP actually minted by the Wombat pool`, violating the invariant that a time restriction on a position must be enforced where the position lives, not in one optional front-end contract and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: the lockedAmount restriction is enforced only inside this helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: withdraw() checks unlockTime and lockedAmount[msg.sender] against this.balance(msg.sender) after unstaking, but the underlying position is ordinary MasterMagpie stake in the same stakingToken, so the restriction is only as strong as this one code path. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: a time restriction on a position must be enforced where the position lives, not in one optional front-end contract; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `withdraw(uint256 _liquidity, uint256 _minAmount)`: constrain the setup so that the pool's deposit token is wBNB and the caller arrived through depositNative, fuzz the attacker inputs (_liquidity, _minAmount and the ordering against the lockedAmount check), and assert after every call that a time restriction on a position must be enforced where the position lives, not in one optional front-end contract.
