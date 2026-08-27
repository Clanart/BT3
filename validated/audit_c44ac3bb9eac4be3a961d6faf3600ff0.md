### Title
Deflationary/Fee-on-Transfer LP Tokens Cause Receipt-Token Over-Minting and Insolvency in `WombatStaking.depositLP()` - (File: wombat/WombatStaking.sol)

### Summary
`WombatStaking.depositLP()` mints `receiptToken` 1:1 against the caller-supplied `_lpAmount` parameter instead of the amount actually received by the contract, unlike the sibling `deposit()` function in the same contract which correctly measures the balance delta before minting. If `poolInfo.lpAddress` is a deflationary/fee-on-transfer or rebasing token, this produces a permanent accounting mismatch between minted receipt tokens (and downstream staked balances in `MasterMagpie`) and the actual LP tokens held/forwarded by `WombatStaking`, leading to insolvency for later withdrawers.

### Finding Description
In `wombat/WombatStaking.sol`, `deposit()` correctly guards against fee-on-transfer/rebasing behavior by measuring the actual LP balance received: [1](#0-0) 

However, `depositLP()`, reachable by any unprivileged pool-helper caller (e.g. `WombatPoolHelper.depositLP()`, `WombatPoolHelperV2.depositLP()`, `AnkrBNBPoolHelper.depositLP()`), does not perform this check. It transfers `_lpAmount` in via `safeTransferFrom`, then unconditionally forwards `_lpAmount` (not the actual received amount) to `_toMasterWomAndSendReward` and mints `IMintableERC20(poolInfo.receiptToken).mint(msg.sender, _lpAmount)`: [2](#0-1) 

If `poolInfo.lpAddress` (the LP token, which is the "wombat pool lp token" received from the underlying Wombat pool and staked here) is deflationary/fee-on-transfer, `WombatStaking` receives less than `_lpAmount`, yet it (a) mints `receiptToken` for the full nominal `_lpAmount`, and (b) records/stakes the nominal `_lpAmount` into the wombat master (`_toMasterWomAndSendReward`). The pool helper (e.g. `WombatPoolHelperV2.depositLP()`) then stakes the delta of its own `stakingToken` (receipt token) balance in `MasterMagpie` via `_stake`, so the user's recorded share in `MasterMagpie`/`BaseRewardPool` also reflects the inflated nominal amount: [3](#0-2) 

This mirrors the reported bug class exactly: a discrepancy between the "recorded staked amount" (receipt tokens minted / MasterMagpie stake accounting) and the "actual reserve" (real LP tokens held by `WombatStaking`), because deposit accounting trusts the nominal transfer amount rather than the measured balance delta.

### Impact Explanation
Because receipt tokens are minted for the full nominal amount while the actual LP reserve is smaller, the total receipt-token supply (and total staked balances tracked by `MasterMagpie`/`BaseRewardPool`) will exceed the real LP token backing held by `WombatStaking`. On `withdraw()`, `WombatStaking` measures actual balance-delta received from the underlying Wombat pool and forwards that to the user: [4](#0-3) 
but pool helpers unstake/burn nominal amounts recorded in `MasterMagpie` and receipt tokens 1:1. Over time this creates a systemic deficit: later users attempting to redeem their full recorded stake will be unable to withdraw the promised amount because the underlying reserve was already depleted by earlier depositors who received receipt tokens/stake credit for LP amounts greater than what was actually custodied — a protocol insolvency condition, consistent with the accepted impact categories.

### Likelihood Explanation
This requires a pool to be registered with a deflationary/fee-on-transfer or rebasing LP token (`registerPool`), which is analogous to the original report's premise that the protocol imposes no restriction preventing such tokens from being used as `depositToken`/`lpAddress`. Any ordinary, unprivileged user calling `depositLP()` through a pool helper triggers the discrepancy — no privileged role, governance action, or external exploit is needed once such a pool exists. The relative likelihood is comparable to the original report (rare/community-dependent token choice), but the mechanism is directly reachable via a plain user transaction.

### Recommendation
In `WombatStaking.depositLP()`, measure the actual LP balance received (before/after `safeTransferFrom`) and use that measured amount — not the caller-supplied `_lpAmount` — for both `_toMasterWomAndSendReward` and the receipt-token mint, consistent with the pattern already used in `deposit()`:
```solidity
uint256 beforeBalance = IERC20(poolInfo.lpAddress).balanceOf(address(this));
IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount);
uint256 lpReceived = IERC20(poolInfo.lpAddress).balanceOf(address(this)) - beforeBalance;

_toMasterWomAndSendReward(_lpAddress, lpReceived, true);
IMintableERC20(poolInfo.receiptToken).mint(msg.sender, lpReceived);
```

### Proof of Concept
1. Admin (or community) registers a pool via `WombatStaking.registerPool()` whose `_lpAddress` is a fee-on-transfer/deflationary token (e.g., taking a 2% fee on every transfer). [5](#0-4) 
2. User A calls `depositLP(1000)` through the pool helper (e.g. `WombatPoolHelperV2.depositLP`), which calls `WombatStaking.depositLP(lpToken, 1000, userA)`. [3](#0-2) 
3. Inside `WombatStaking.depositLP()`, `safeTransferFrom(userA, address(this), 1000)` actually delivers only 980 tokens to `WombatStaking` (2% fee burned/redirected), but the function still calls `_toMasterWomAndSendReward(lpToken, 1000, true)` and mints `1000` `receiptToken` to `msg.sender`. [2](#0-1) 
4. The pool helper stakes the full 1000 receipt tokens for User A in `MasterMagpie`, so User A's recorded stake (1000) now exceeds the real reserve backing (980) by 20 tokens.
5. As more deposits occur, the aggregate deficit between recorded stakes/receipt-token supply and real LP token reserve compounds. When users later call `withdraw()`, `WombatStaking` can only pay out based on actual balance received from the underlying Wombat pool, so the last withdrawers cannot redeem their full recorded/staked balance — protocol insolvency for the affected pool.

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

**File:** wombat/WombatStaking.sol (L420-429)
```text
    /// @notice Register a new Pool on Wombat Staking and Master Magpie
    /// @dev this function will deploy a new WombatPoolHelper, and add the Pool to the masterMagpie
    /// @param _pid the pid of the Pool on master wombat
    /// @param _depositToken the token to stake in the wombat Pool
    /// @param _lpAddress the address of the recepit token after deposit into wombat Pool. Also used for the pool identifier on WombatStaking
    /// @param _depositTarget the address to deposit for alt Pool
    /// @param _receiptName the name of the receipt Token
    /// @param _receiptSymbol the symbol of the receipt Token    
    /// @param _allocPoints the weight of the MGP allocation
    function registerPool(
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
