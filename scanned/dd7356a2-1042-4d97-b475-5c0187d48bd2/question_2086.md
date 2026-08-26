# Q2086: WombatStaking.deposit - deposit credits a balance delta as the receipt mint

## Question
wombat/WombatStaking.sol: deposit() mints lpReceived = balanceOf(lpAddress) after minus before to msg.sender, so LP tokens that reach WombatStaking for any other reason during that window are converted into receipt tokens for the depositing helper. Under a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, is there an unprivileged sequence of `deposit(address,uint256,uint256,address,address) via a pool helper` that leaves `IERC20(wom).balanceOf(address(this))` unreconciled with `totalConverted in mWOM`, violates the invariant that receipt tokens minted must correspond exactly to LP the depositor supplied, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: deposit credits a balance delta as the receipt mint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: deposit() mints lpReceived = balanceOf(lpAddress) after minus before to msg.sender, so LP tokens that reach WombatStaking for any other reason during that window are converted into receipt tokens for the depositing helper. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: receipt tokens minted must correspond exactly to LP the depositor supplied; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, call `deposit(address,uint256,uint256,address,address) via a pool helper`, and assert `IERC20(wom).balanceOf(address(this))` equals `totalConverted in mWOM` and that no account can withdraw more than it put in.
