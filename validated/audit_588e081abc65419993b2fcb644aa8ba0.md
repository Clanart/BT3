### Title
Spot-price manipulation of the WOM/mWOM Wombat pool lets an attacker skim protocol reward yield via `SmartWomConvert.smartConvert` - (File: `wombat/SmartWomConvert.sol`)

### Summary
`SmartWomConvert` decides how much of the harvested WOM protocol fee is swapped for mWOM (vs. minted 1:1) by reading the **current spot price** of the WOM/mWOM Wombat pool through `IWombatRouter.getAmountOut`, and then executes the actual swap with **zero slippage protection**. Because the triggering entry point, `WombatStaking.harvest`, is completely permissionless, an attacker can manipulate the pool's spot price in the same transaction, force the protocol's fee-conversion swap to execute at the manipulated price, and reverse the manipulation afterward — extracting value that should have flowed to the reward pools (mWOM/vlMGP holders' yield).

### Finding Description
`currentRatio()` reads a live spot quote from the router against a single Wombat pool: [1](#0-0) 

This spot value directly determines `convertRatio` inside `smartConvert`, i.e., how much of the incoming WOM is swapped through the pool versus minted 1:1 via `IMWom.deposit`: [2](#0-1) 

The actual swap in `_convertFor` is executed with `amountOutMin = 0` (no per-call slippage bound); the only protection is a coarse floor check after the swap that merely requires the total received mWOM to be at least equal to the WOM amount in (`_minRec = _amountIn` when called from `smartConvert`): [3](#0-2) 

This is triggered from `WombatStaking._sendRewards`, which is reached from `WombatStaking.harvest`, a function with **no caller restriction** (`_onlyActivePool` only checks the pool's active flag, not `msg.sender`): [4](#0-3) [5](#0-4) 

This is the same bug class as the reported `calc_withdraw_one_coin` issue: a fund-flow decision (here, how the protocol converts/values its own WOM fee) is derived from a manipulable single-block AMM spot price instead of a manipulation-resistant reference, and the resulting on-chain swap has no independent slippage guard beyond a weak `>= amountIn` floor.

### Impact Explanation
An attacker who manipulates the WOM/mWOM pool immediately before invoking the permissionless `harvest` function can cause the protocol's own conversion swap to execute at an attacker-favorable price. The only post-check (`convertAmount + amountRec >= _amountIn`) does not enforce a fair market rate — it only prevents an outright net loss below 1:1 — so the attacker can capture the price-impact margin that should have accrued to the protocol as normal buyback value. Since this WOM fee amount is destined for `IBaseRewardPool.queueNewRewards` (i.e., is unclaimed yield for stakers/lockers), value is siphoned away from that yield stream to the attacker. This is a theft of protocol/user yield, not merely a fee-router inefficiency.

### Likelihood Explanation
`harvest` is permissionless and callable by any wallet at any time reward fees have accrued, and the WOM/mWOM Wombat pool spot price is directly readable and swappable by anyone, including via flash loan, within the same transaction. No privileged role, governance action, or external protocol compromise is required — only capital to move the pool's spot price momentarily, which is returned/profited within the same atomic transaction.

### Recommendation
- Short term: do not derive `convertRatio`/`currentRatio` purely from a single-block `getAmountOut` spot query; require a TWAP, external oracle, or a maximum-deviation check against a trusted reference price before allowing the swap path.
- Set a meaningful `amountOutMin` on the internal `swapExactTokensForTokens` call in `_convertFor` (not `0`), and consider capping the price impact per call regardless of the post-hoc `_minRec` floor.
- Consider restricting or rate-limiting how frequently/how much `smartConvert`'s swap path can be triggered per block to reduce single-transaction manipulation profitability.

### Proof of Concept
1. Attacker flash-loans a large amount of WOM or mWOM.
2. Attacker swaps against `womMWomPool` via `IWombatRouter` to push `currentRatio()` in `SmartWomConvert` favorably (e.g., below `buybackThreshold`), forcing a larger buyback swap path in the next call.
3. Attacker calls `WombatStaking.harvest(lpToken)` for any active pool with an accrued WOM reward and a nonzero `isMWOM` fee; this triggers `_sendRewards` → `IConverter(smartWomConverter).smartConvert(feeAmount, 0)`.
4. Inside `_convertFor`, `IWombatRouter.swapExactTokensForTokens(..., 0, ...)` executes at the manipulated price; the only guard is `convertAmount + amountRec >= _amountIn`, which the attacker keeps just satisfied.
5. Attacker reverses the initial pool trade, restoring price and repaying the flash loan, retaining the price-impact margin extracted from the protocol's conversion — value that should have been queued as reward-pool yield via `IBaseRewardPool.queueNewRewards`.

### Citations

**File:** wombat/SmartWomConvert.sol (L107-117)
```text
    function currentRatio() public view returns (uint256) {
        address[] memory tokenPath = new address[](2);
        tokenPath[0] = mWom;
        tokenPath[1] = wom;
        
        address[] memory poolPath = new address[](1);
        poolPath[0] = womMWomPool;
    
        (uint256 amountOut, ) = IWombatRouter(router).getAmountOut(tokenPath, poolPath, 1e18);
        return amountOut * DENOMINATOR / 1e18;
    }
```

**File:** wombat/SmartWomConvert.sol (L133-147)
```text
    function smartConvert(uint256 _amountIn, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        if (_amountIn == 0) revert MustNoBeZero();

        uint256 convertRatio = DENOMINATOR;
        uint256 mWomToWom = currentRatio();

        if (mWomToWom < buybackThreshold) {
            uint256 maxSwap = maxSwapAmount();
            uint256 amountToSwap = _amountIn > maxSwap ? maxSwap : _amountIn;
            uint256 convertAmount = _amountIn - amountToSwap;
            convertRatio = convertAmount * DENOMINATOR / _amountIn;
        }

        return _convertFor(_amountIn, convertRatio, _amountIn, msg.sender, _mode);
    }
```

**File:** wombat/SmartWomConvert.sol (L186-207)
```text
        if (buybackAmount > 0) {
            address[] memory tokenPath = new address[](2);
            tokenPath[0] = wom;
            tokenPath[1] = mWom;
            address[] memory poolPath = new address[](1);
            poolPath[0] = womMWomPool;
        
            IERC20(wom).safeApprove(router, buybackAmount);
            amountRec = IWombatRouter(router).swapExactTokensForTokens(
                tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp
            );
        }

        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();

        obtainedmWomAmount = convertAmount + amountRec;
```

**File:** wombat/WombatStaking.sol (L329-335)
```text
    /// @notice harvest a Pool from Wombat
    /// @param _lpToken wombat pool lp as helper identifier
    function harvest(
        address _lpToken
    ) whenNotPaused _onlyActivePool(_lpToken) external {
        _toMasterWomAndSendReward(_lpToken, 0, true); // triggers harvest from wombat exchange
    }
```

**File:** wombat/WombatStaking.sol (L739-753)
```text
                    if (feeInfo.isMWOM && rewardToken == wom) {
                        if (smartWomConverter != address(0)) {
                            IERC20(wom).safeApprove(smartWomConverter, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IConverter(smartWomConverter).smartConvert(feeAmount, 0);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
                        } else {
                            IERC20(wom).safeApprove(mWom, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IMWom(mWom).deposit(feeAmount);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
                        }
                    }
```
