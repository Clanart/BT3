### Title
Inconsistent LP/receipt-token accounting when depositing fee-on-transfer or deflationary LP tokens via `depositLP` - ([File: wombat/WombatStaking.sol])

### Summary
`WombatStaking.depositLP` transfers the wombat pool LP token from the caller and then mints the corresponding `receiptToken` using the requested `_lpAmount` directly, instead of measuring the actual amount of LP tokens received by the contract. This mirrors the reported bug class where a `safeTransferFrom` amount is assumed to equal the amount actually received, which breaks for fee-on-transfer/deflationary tokens.

### Finding Description
In `depositLP`, the contract does: [1](#0-0) 

```
IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount);
_toMasterWomAndSendReward(_lpAddress, _lpAmount, true);
IMintableERC20(poolInfo.receiptToken).mint(msg.sender, _lpAmount);
```

`_lpAmount` (the pre-transfer requested amount) is used both to stake into master wombat and to mint the `receiptToken`, without checking the actual LP balance delta on the contract. This is inconsistent with the sibling `deposit` function in the very same contract, which correctly measures the actually-received LP amount via a before/after balance diff before minting: [2](#0-1) 

If `poolInfo.lpAddress` (or any LP token onboarded for a pool) applies a transfer fee or is a deflationary/rebasing token, the amount actually custodied by `WombatStaking` will be less than `_lpAmount`, yet `receiptToken` is minted 1:1 with the full `_lpAmount`. This directly inflates the `receiptToken` supply relative to the LP tokens actually backing it.

This is reachable from an ordinary wallet through `WombatPoolHelper.depositLP` / `WombatPoolHelperV2.depositLP`, which forward user calls straight into `WombatStaking.depositLP`: [3](#0-2) 

### Impact Explanation
Because `receiptToken` is over-minted relative to actual LP custody, the pool becomes under-collateralized: legitimate users attempting `withdraw` later (which relies on `IWombatPool(poolInfo.depositTarget).withdraw` backed by the LP tokens actually held) can fail once the deficit is exposed, permanently freezing/impairing other users' funds and creating protocol insolvency for that pool. This matches the accepted impact categories (protocol insolvency / permanent freezing of user funds).

### Likelihood Explanation
Likelihood depends on whether an LP token onboarded to `WombatStaking` pools is (or becomes) fee-on-transfer or deflationary — the code path itself is always exploitable in that scenario, and no privileged action is required; any ordinary depositor using `depositLP` triggers the inconsistency. The root-cause code pattern is present and directly contradicts the safer pattern already used elsewhere in the same file (`deposit`), showing awareness of the risk was inconsistently applied.

### Recommendation
Mirror the pattern already used in `deposit`: measure the LP token balance of `WombatStaking` before and after the `safeTransferFrom` call in `depositLP`, and use that delta (not the raw `_lpAmount`) both for `_toMasterWomAndSendReward` and for `IMintableERC20(poolInfo.receiptToken).mint`.

### Proof of Concept
1. Onboard a pool whose `lpAddress` token charges a transfer fee (or is deflationary).
2. A user calls `WombatPoolHelper.depositLP(_lpAmount)` → `WombatStaking.depositLP(lpToken, _lpAmount, user)`.
3. `safeTransferFrom` moves `_lpAmount` from user's perspective, but `WombatStaking` actually receives `_lpAmount - fee`.
4. `receiptToken` is minted for the full `_lpAmount`, exceeding the LP tokens actually held.
5. Repeating this drains the effective LP backing below the minted receipt token supply, so a subsequent `withdraw` by another user fails or under-delivers once the shortfall is reached. [1](#0-0)

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
