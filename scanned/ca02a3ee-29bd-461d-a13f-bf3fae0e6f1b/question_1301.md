# Q1301: WombatPoolHelperV2.depositLP - receipt-token delta credited to an attacker-chosen beneficiary

## Question
Note that in wombat/WombatPoolHelperV2.sol, _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Can an attacker holding only tokens bought on market reach it via `depositLP(uint256 _lpAmount)` under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested and force `IERC20(stakingToken).totalSupply()` apart from `the MasterWombat staked balance for pid`, breaking the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount) under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, asserting on every row that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution.
