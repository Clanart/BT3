# Q1187: WombatPoolHelperV2.depositFor - receipt-token delta credited to an attacker-chosen beneficiary

## Question
In wombat/WombatPoolHelperV2.sol, _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Starting from a state where the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, can an unprivileged EOA use `depositFor(uint256 _amount, address _for)` to leave `this.balance(msg.sender)` inconsistent with `lockedAmount[msg.sender]`, violating the invariant that the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: receipt-token delta credited to an attacker-chosen beneficiary)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: _deposit() measures afterDeposit - beforeDeposit on the helper's own stakingToken balance and stakes that delta for _for, while WombatStaking mints the receipt token to msg.sender, so the amount minted and the account credited are decided in two separate places. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: the receipt tokens minted for a deposit and the MasterMagpie credit for that deposit must be one atomic attribution; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for)` sequence atomically under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, asserting at the end that `this.balance(msg.sender)` still equals `lockedAmount[msg.sender]` and the PoC's balance delta is non-positive.
