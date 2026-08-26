# Q4123: mWOM.deposit - deposit mints mWOM without locking the WOM

## Question
In wombat/mWOM.sol, the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Can an unprivileged attacker reach this through `deposit(uint256 _amount)` while helper is unset so convertAndStake reverts and only the plain mint path is reachable, and drive `IERC20(this).totalSupply()` out of agreement with `IERC20(wom).balanceOf(wombatStaking) + veWom backing` - breaking the invariant that every mWOM in circulation must be backed by WOM the protocol has actually committed - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: deposit mints mWOM without locking the WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Precondition: helper is unset so convertAndStake reverts and only the plain mint path is reachable.
- Invariant to test: every mWOM in circulation must be backed by WOM the protocol has actually committed; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under helper is unset so convertAndStake reverts and only the plain mint path is reachable, then assert `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing` end identical in both runs.
