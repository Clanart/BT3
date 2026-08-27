## Title
Fee-on-transfer LP tokens break receipt token accounting in `WombatStaking.depositLP` - ([File: wombat/WombatStaking.sol])

### Summary
`WombatStaking.depositLP()` mints receipt/staking tokens based on the caller-supplied `_lpAmount` parameter instead of the actual LP tokens received after `safeTransferFrom`, unlike the sibling `deposit()` function which correctly measures the actual amount received via a before/after balance diff. If the pool's LP token (or an asset registered as a pool) charges a transfer fee, this causes the protocol to mint more receipt tokens than LP tokens actually held, permanently under-collateralizing user withdrawals.

### Finding Description
In `WombatStaking.deposit()`, the protocol correctly guards against amount mismatches by measuring the actual LP tokens received from the Wombat pool via a before/after balance diff, then minting the receipt token based on that measured amount: [1](#0-0) 

However, `depositLP()` does not apply the same defensive pattern. It transfers `_lpAmount` from `_for` using `safeTransferFrom`, but then mints the receipt token using the raw `_lpAmount` input parameter rather than the actual amount received by the contract: [2](#0-1) 

If the underlying LP/asset token deducts a fee on transfer, `IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount)` causes `WombatStaking` to receive strictly less than `_lpAmount`, yet `IMintableERC20(poolInfo.receiptToken).mint(msg.sender, _lpAmount)` still mints the full, un-discounted amount. The caller here is the pool helper (e.g. `WombatPoolHelper`/`WombatPoolHelperV2`), which then stakes that inflated receipt-token amount into `MasterMagpie` on behalf of the user: [3](#0-2) [4](#0-3) 

This mirrors the reported bug class: functions assume tokens transfer exactly the specified amount and don't verify/measure actual received balances, so fee-on-transfer tokens break the accounting invariant (here, minting more receipt tokens than the actual backing LP balance) instead of merely reverting as in the original Timeswap report.

### Impact Explanation
Every `depositLP` call with a fee-charging LP token mints receipt/staking tokens (and downstream `MasterMagpie` shares) that exceed the actual LP tokens custodied by `WombatStaking`. This is a protocol insolvency condition: the sum of receipt tokens minted no longer matches the LP tokens actually held, so `withdraw()` (which redeems real Wombat pool liquidity and burns receipt tokens 1:1) will eventually fail for some users once the shortfall is exposed, permanently freezing a portion of user funds.

### Likelihood Explanation
The bug triggers automatically and repeatably any time `depositLP` is invoked with an LP/asset token that has a transfer fee, requiring no privileged action — any ordinary wallet calling `WombatPoolHelper.depositLP`/`WombatPoolHelperV2.depositLP` on such a pool triggers it. Likelihood depends on whether a fee-on-transfer token is ever registered as a pool's `lpAddress`/`depositToken`, which is a pool-configuration matter rather than a privileged exploit path from the depositor's perspective.

### Recommendation
In `WombatStaking.depositLP()`, measure the actual amount of LP tokens received (before/after balance diff on `poolInfo.lpAddress`) the same way `deposit()` does, and mint the receipt token / forward that measured amount instead of the raw `_lpAmount` parameter.

### Proof of Concept
1. Register a pool whose `lpAddress` implements a transfer fee (e.g., deducts 1% on `transferFrom`).
2. User calls `WombatPoolHelper.depositLP(1000)`, which calls `WombatStaking.depositLP(lpToken, 1000, user)`.
3. `IERC20(poolInfo.lpAddress).safeTransferFrom(user, address(this), 1000)` results in `WombatStaking` actually receiving only 990 LP tokens due to the fee.
4. `WombatStaking` still executes `IMintableERC20(poolInfo.receiptToken).mint(poolHelper, 1000)`, over-minting the receipt/staking token by 10 relative to actual backing.
5. `WombatPoolHelper` stakes the full 1000 receipt tokens into `MasterMagpie` for the user, who now holds shares that are not fully collateralized, contributing to eventual withdrawal shortfalls for the pool.

### Citations

**File:** wombat/WombatStaking.sol (L254-269)
```text
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

**File:** wombat/WombatPoolHelperV2.sol (L109-116)
```text
    function depositLP(uint256 _lpAmount) external {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).depositLP(lpToken, _lpAmount, msg.sender);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewLpDeposit(msg.sender, _lpAmount);
    }
```
