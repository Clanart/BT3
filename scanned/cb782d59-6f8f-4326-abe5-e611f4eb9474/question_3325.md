# Q3325: mWOM.deposit - deposit mints mWOM without locking the WOM

## Question
Consider wombat/mWOM.sol, where the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Assuming the veWOM mint returns less than the WOM supplied because of the lockDays curve, can an unprivileged attacker turn this into a divergence between `IERC20(wom).balanceOf(address(this))` and `totalConverted` via `deposit(uint256 _amount)`, breaking the invariant that every mWOM in circulation must be backed by WOM the protocol has actually committed and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: deposit mints mWOM without locking the WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: every mWOM in circulation must be backed by WOM the protocol has actually committed; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the veWOM mint returns less than the WOM supplied because of the lockDays curve, call `deposit(uint256 _amount)`, and assert `IERC20(wom).balanceOf(address(this))` equals `totalConverted` and that no account can withdraw more than it put in.
