# Q1124: mWOM.deposit - deposit mints mWOM without locking the WOM

## Question
wombat/mWOM.sol: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. With _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked under attacker control and rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, can an unprivileged caller sequence `deposit(uint256 _amount)` so that `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing` no longer reconcile, violating the invariant that every mWOM in circulation must be backed by WOM the protocol has actually committed and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: deposit mints mWOM without locking the WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: every mWOM in circulation must be backed by WOM the protocol has actually committed; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, call `deposit(uint256 _amount)`, and assert `IERC20(this).totalSupply()` equals `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and that no account can withdraw more than it put in.
