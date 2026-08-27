### Title
`WombatStaking.depositLP()` mints receipt tokens based on nominal transfer amount, not amount actually received — breaks on fee-on-transfer LP tokens - (File: wombat/WombatStaking.sol)

### Summary
`WombatStaking.deposit()` correctly measures the actual LP tokens received from the Wombat pool via a before/after balance diff before minting receipt tokens, but the sibling function `WombatStaking.depositLP()` skips this check and instead mints receipt tokens and stakes based on the caller-supplied `_lpAmount` parameter directly, assuming the `safeTransferFrom` delivered exactly that amount.

### Finding Description
In `deposit()`, the amount of LP actually obtained is computed defensively: [1](#0-0) 

However, `depositLP()` — reachable by any ordinary wallet through `WombatPoolHelper.depositLP()` / `WombatPoolHelperV2.depositLP()` — transfers the LP token from the user and then immediately uses the caller-supplied `_lpAmount` (not a measured balance delta) both to stake into MasterWombat and to mint receipt tokens: [2](#0-1) 

If `poolInfo.lpAddress` is (or later becomes, e.g. via a proxy upgrade) a fee-on-transfer/deflationary token, `IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount)` will deliver less than `_lpAmount` to `WombatStaking`. The subsequent `_toMasterWomAndSendReward(_lpAddress, _lpAmount, true)` call and `IMintableERC20(poolInfo.receiptToken).mint(msg.sender, _lpAmount)` still operate on the full nominal `_lpAmount`, over-crediting the depositor with receipt tokens beyond the LP actually custodied/staked on their behalf.

### Impact Explanation
Receipt token supply becomes systematically larger than the real underlying LP balance held/staked by the protocol. Because receipt tokens are the unit of account for subsequent `withdraw()` calls (which burn receipt tokens 1:1 against real LP redeemed from the Wombat pool), this mismatch causes the pool to become unable to honor all receipt-token holders' withdrawals once the shortfall accumulates — a protocol insolvency / permanent fund-freezing condition for the affected pool, not merely a reverting transaction.

### Likelihood Explanation
This requires `poolInfo.lpAddress` to be a fee-on-transfer token. Wombat Asset LP tokens used in production are not known to charge transfer fees today, mirroring the disputed nature of the original report, but the same mitigating token can add such a mechanism later (as the judge in the original report noted for USDT-like tokens), and the `depositLP` path is fully reachable by any unprivileged wallet holding the relevant LP token — no admin/governance action is required to trigger the divergent, unguarded accounting path itself.

### Recommendation
Mirror the pattern already used in `deposit()`: measure the actual LP balance received via a before/after diff in `depositLP()`, and use that measured amount (not the caller-supplied `_lpAmount`) for both `_toMasterWomAndSendReward` and the receipt token mint.

### Proof of Concept
1. Admin configures a pool whose `lpAddress` is (or is later upgraded to be) a fee-on-transfer ERC20.
2. User calls `WombatPoolHelper.depositLP(_lpAmount)` → `WombatStaking.depositLP(_lpAddress, _lpAmount, _for)`.
3. `safeTransferFrom` delivers `_lpAmount - fee` to `WombatStaking`, but the code stakes/mints against the full `_lpAmount`.
4. Repeated deposits cause `receiptToken` total supply to exceed the real LP custodied, so eventually not all receipt-token holders can be redeemed on `withdraw()`. [2](#0-1)

### Citations

**File:** wombat/WombatStaking.sol (L255-269)
```text
        uint256 beforeBalance = IERC20(poolInfo.lpAddress).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).deposit(
            depositToken,
            _amount,
            _minimumLiquidity,
            address(this),
            block.timestamp,
            false
        );

        uint256 lpReceived = IERC20(poolInfo.lpAddress).balanceOf(address(this)) - beforeBalance;
        _toMasterWomAndSendReward(_lpAddress, lpReceived, true); // triggers harvest from wombat exchange
        // update variables
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, lpReceived);
        emit NewDeposit(_for, depositToken, _amount, poolInfo.receiptToken, lpReceived);
```

**File:** wombat/WombatStaking.sol (L272-287)
```text
    function depositLP(
        address _lpAddress,
        uint256 _lpAmount,
        address _for
    ) nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external {
        // Get information of the Pool of the token
        Pool storage poolInfo = pools[_lpAddress];

        // Transfer lp to this contract and stake it to wombat
        IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount);

        _toMasterWomAndSendReward(_lpAddress, _lpAmount, true); // triggers harvest from wombat exchange
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, _lpAmount);

        emit NewLPDeposit(_for, poolInfo.lpAddress, _lpAmount, poolInfo.receiptToken, _lpAmount);
    }
```
