# Q0323: AnkrBNBPoolHelper.depositLP - no reentrancy guard anywhere on the helper

## Question
Note that in wombat/AnkrBNBPoolHelper.sol, none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Can an attacker holding only tokens bought on market reach it via `depositLP(uint256 _lpAmount)` under the pool's deposit token is wBNB and the caller arrived through depositNative and force `IERC20(stakingToken).balanceOf(address(this)) delta` apart from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, breaking the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard for Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `depositLP(uint256 _lpAmount)` sequence atomically under the pool's deposit token is wBNB and the caller arrived through depositNative, asserting at the end that `IERC20(stakingToken).balanceOf(address(this)) delta` still equals `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` and the PoC's balance delta is non-positive.
