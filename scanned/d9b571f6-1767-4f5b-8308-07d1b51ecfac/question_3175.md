# Q3175: WombatPoolHelperV2.deposit - deposit and withdraw both run the full harvest and fee path

## Question
Note that in wombat/WombatPoolHelperV2.sol, WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Can an attacker holding only tokens bought on market reach it via `deposit(uint256 _amount, uint256 _minimumLiquidity)` under a residual stakingToken balance from an earlier rounding sits on the helper and force `IERC20(stakingToken).balanceOf(address(this)) delta` apart from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, breaking the invariant that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: deposit and withdraw both run the full harvest and fee path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange a residual stakingToken balance from an earlier rounding sits on the helper, call `deposit(uint256 _amount, uint256 _minimumLiquidity)`, and assert `IERC20(stakingToken).balanceOf(address(this)) delta` equals `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` and that no account can withdraw more than it put in.
