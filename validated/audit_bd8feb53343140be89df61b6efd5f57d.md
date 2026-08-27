Confirmed: `WombatStaking.deposit()` correctly uses a before/after balance-diff pattern to compute `lpReceived` before minting receipt tokens [1](#0-0) , but `depositLP()` does not — it mints receipt tokens 1:1 with the caller-supplied `_lpAmount` parameter without ever measuring the actual balance received by the contract via `safeTransferFrom` [2](#0-1) .

### Title
WombatStaking.depositLP() mints receipt tokens based on nominal transfer amount instead of measured balance change, allowing insolvency with fee-on-transfer/deflationary LP tokens - (File: wombat/WombatStaking.sol)

### Summary
`WombatStaking.depositLP()` pulls `_lpAmount` of `poolInfo.lpAddress` from the caller via `safeTransferFrom(_for, address(this), _lpAmount)` and then unconditionally mints `_lpAmount` of `receiptToken` to `msg.sender` (the pool helper, on behalf of the depositor) [3](#0-2) . Unlike the sibling `deposit()` function, which explicitly measures `beforeBalance`/`afterBalance` on `poolInfo.lpAddress` to derive `lpReceived` before minting [4](#0-3) , `depositLP()` assumes the transferred nominal amount always equals the amount actually credited to the contract.

### Finding Description
This is the same class of bug flagged in the external report: the system assumes an ERC20 `transferFrom` of amount X always increases the contract's balance by exactly X, and does not validate or track deviations from that assumption for tokens whose transfer semantics can change or diverge from the naive model (fee-on-transfer, deflationary, or otherwise non-standard-behaving tokens) [5](#0-4) . `WombatPoolHelper.depositLP()` and `WombatPoolHelperV2.depositLP()` call into `WombatStaking.depositLP()` and then stake the resulting receipt-token balance delta into MasterMagpie, propagating whatever receipt-token amount was minted by `WombatStaking` [6](#0-5) . If the underlying `lpAddress` token ever transfers less than the requested `_lpAmount` (fee-on-transfer, rebasing/deflationary behavior, or a future upgrade to such semantics), `WombatStaking` mints receipt tokens for value it never actually custodied.

### Impact Explanation
Because receipt tokens are burned 1:1 for `_liquidity` on withdraw and that liquidity is pulled from the pool's real LP holdings in `WombatStaking.withdraw()` [7](#0-6) , over-minting receipt tokens via `depositLP()` creates a permanent mismatch between the outstanding receipt-token supply (staked, redeemable claims) and the actual LP tokens held by `WombatStaking`. Later depositors/withdrawers competing for the same underlying LP pool can be left unable to redeem, resulting in insolvency of the pool and loss of funds for other legitimate depositors — the last users to attempt `withdraw()` after the deficiency has been introduced will find the contract lacks sufficient LP balance.

### Likelihood Explanation
Exploitability depends on `poolInfo.lpAddress` behaving as anything other than a standard fixed-transfer ERC20 (fee-on-transfer, deflationary, or upgraded to such semantics), which the protocol does not validate against, mirroring exactly the "blindly trusts upgradeable/nonstandard ERC20" bug class from the referenced report. Any ordinary wallet holding such an LP token and calling `depositLP()` through `WombatPoolHelper`/`WombatPoolHelperV2` can trigger it without any privileged role.

### Recommendation
Mirror the pattern already used in `deposit()`: measure `IERC20(poolInfo.lpAddress).balanceOf(address(this))` before and after the `safeTransferFrom` in `depositLP()`, and mint `receiptToken` (and forward to `_toMasterWomAndSendReward`) based on the actual measured delta rather than the caller-supplied `_lpAmount`.

### Proof of Concept
1. A pool is registered with `lpAddress` set to a token that charges a transfer fee or is deflationary (or is later upgraded to become so).
2. An attacker calls `WombatPoolHelper.depositLP(_lpAmount)` → `WombatStaking.depositLP(lpToken, _lpAmount, msg.sender)` [2](#0-1) .
3. `safeTransferFrom` moves only `_lpAmount - fee` tokens into `WombatStaking`, but `IMintableERC20(poolInfo.receiptToken).mint(msg.sender, _lpAmount)` mints the full nominal `_lpAmount` of receipt tokens.
4. The attacker's pool helper stakes the full minted receipt-token amount into MasterMagpie via `_stake` [8](#0-7) , obtaining claims exceeding what was actually deposited.
5. When the attacker (or subsequent users) call `withdraw()`, `WombatStaking.withdraw()` attempts to pull `_liquidity` worth of `poolInfo.lpAddress` from the Wombat pool [9](#0-8) , but the pool's real LP backing is short by the fee amount, eventually causing later withdrawals to fail/revert, freezing funds for other depositors.

### Citations

**File:** wombat/WombatStaking.sol (L252-269)
```text
        IERC20(depositToken).safeTransferFrom(_from, address(this), _amount);

        IERC20(depositToken).safeApprove(poolInfo.depositTarget, _amount);
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

**File:** wombat/WombatStaking.sol (L295-321)
```text
    function withdraw(
        address _lpToken,
        uint256 _liquidity,
        uint256 _minAmount,
        address _sender
    ) nonReentrant whenNotPaused _onlyPoolHelper(_lpToken) external {
        Pool storage poolInfo = pools[_lpToken];

        IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity);
        _toMasterWomAndSendReward(_lpToken, _liquidity, false);

        uint256 beforeWithdraw = IERC20(poolInfo.depositToken).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).withdraw(
            poolInfo.depositToken,
            _liquidity,
            _minAmount,
            address(this),
            block.timestamp
        );

        IERC20(poolInfo.depositToken).safeTransfer(
            _sender,
            IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw
        );

        emit NewWithdraw(_sender, poolInfo.depositToken, _liquidity);
    }
```

**File:** wombat/WombatPoolHelper.sol (L102-109)
```text
    function depositLP(uint256 _lpAmount) external {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).depositLP(lpToken, _lpAmount, msg.sender);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewLpDeposit(msg.sender, _lpAmount);
    }
```

**File:** wombat/WombatPoolHelper.sol (L161-165)
```text
    /// @notice stake the receipt token in the masterchief of GMP on behalf of the caller
    function _stake(uint256 _amount, address _sender) internal {
        IERC20(stakingToken).safeApprove(masterMagpie, _amount);
        IMasterMagpie(masterMagpie).depositFor(stakingToken, _amount, _sender);
    }
```
