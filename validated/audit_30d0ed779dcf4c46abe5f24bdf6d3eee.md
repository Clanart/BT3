### Title
Flash-loan price manipulation of the WOM/mWOM buyback swap during fee conversion (zero slippage protection) - ([File: wombat/SmartWomConvert.sol])

### Summary
`WombatStaking.harvest()` is a permissionless function that any wallet can call to trigger reward harvesting. This flow eventually calls `SmartWomConvert.smartConvert()` → `_convertFor()`, which swaps a portion of the protocol's collected WOM fees for mWOM through the Wombat AMM pool (`womMWomPool`) using a hardcoded `minAmountOut` of `0`. Because the swap has no slippage protection and relies entirely on the pool's instantaneous cash/liability state, an attacker can manipulate `womMWomPool`'s spot price in the same transaction (via a large swap or flash loan) and then trigger the harvest path, forcing the protocol to swap its fee funds at an attacker-favorable rate.

### Finding Description
`WombatStaking.harvest()` calls `_toMasterWomAndSendReward`, which is externally reachable and only gated by `_onlyActivePool` (a pool-status check, not a caller-permission check): [1](#0-0) 

This routes into `_sendRewards`, which, when the fee is flagged `isMWOM` and a `smartWomConverter` is set, calls `IConverter(smartWomConverter).smartConvert(feeAmount, 0)`: [2](#0-1) 

Inside `SmartWomConvert`, `smartConvert()` derives a `convertRatio` from `currentRatio()` (a live spot-price read from `womMWomPool` via `IWombatRouter.getAmountOut`) and `maxSwapAmount()` (derived from the pool's live `cash()`/`liability()`), both of which are directly manipulable by swapping into/out of the pool in the same or a preceding transaction: [3](#0-2) 

The actual swap executed in `_convertFor` uses a hardcoded `0` for `minAmountOut`, removing any protection against the manipulated price: [4](#0-3) 

Because `harvest()` can be invoked by any unprivileged wallet (via `WombatPoolHelper.harvest()` / `WombatPoolHelperV2.harvest()`, which simply forward to `IWombatStaking.harvest(lpToken)`), an attacker can: [5](#0-4) 

1. Swap a large amount into/out of `womMWomPool` to skew its cash/liability ratio and depress the mWOM/WOM spot price.
2. Call `harvest()` on the affected pool, forcing `WombatStaking` to route accumulated WOM protocol fees through `SmartWomConvert`'s zero-slippage swap at the manipulated price.
3. Reverse the initial swap, capturing the value that was extracted from the protocol's fee/reward funds at the bad execution price.

This mirrors the Nmbplatform bug class referenced in the external report: an unprivileged actor manipulates an AMM's spot price and then triggers a downstream contract function that consumes that spot price/executes a swap without adequate protection, extracting value that belongs to other users/the protocol.

### Impact Explanation
The WOM fees being converted are destined for `mWOM` rewards distributed to stakers (via `queueNewRewards` to `BaseRewardPool`) or bribe fee recipients. When the buyback swap executes at a manipulated unfavorable rate, real value is permanently removed from what would have been distributed to legitimate depositors/voters as yield, and captured by the attacker. This constitutes theft of unclaimed yield / protocol funds reachable by any wallet without needing privileged access.

### Likelihood Explanation
`harvest()` is unauthenticated and callable by anyone at will, and the vulnerable swap path (`isMWOM` fee handling with `smartWomConverter` set) executes automatically whenever WOM rewards are harvested for a pool. The manipulation only requires temporarily skewing `womMWomPool`'s cash/liability balance, achievable with a flash loan or large capital swap, then immediately calling `harvest()` — a straightforward, repeatable, single-transaction attack requiring no special permissions.

### Recommendation
Replace the hardcoded `0` minimum-output in the buyback swap inside `SmartWomConvert._convertFor` with a caller/config-supplied minimum-received amount validated against a time-weighted or otherwise manipulation-resistant reference price, and/or restrict who can trigger the harvest-driven fee-conversion path (e.g., only via a controlled keeper, or by having `_sendRewards`'s smart-convert call pass through the same `_minRec` protections exposed on the public `convert`/`convertFor` entry points).

### Proof of Concept
1. Attacker takes a flash loan and performs a large swap against `womMWomPool` to depress the mWOM/WOM exchange rate (or drive `cash()` far below `liability()`).
2. Attacker calls `WombatPoolHelper.harvest()` (or `WombatPoolHelperV2.harvest()`) for a pool with accumulated WOM fees and `isMWOM` fee flag set, which calls `WombatStaking.harvest()` → `_toMasterWomAndSendReward` → `_sendRewards` → `SmartWomConvert.smartConvert(feeAmount, 0)`.
3. `smartConvert` computes an inflated `maxSwapAmount`/`convertRatio` based on the manipulated pool state and executes `IWombatRouter.swapExactTokensForTokens(..., 0, ...)` — a zero-slippage swap — converting protocol WOM fees to mWOM at the attacker-manipulated bad rate.
4. Attacker reverses their initial swap in `womMWomPool`, restoring the price and capturing the value differential extracted from the protocol's fee conversion, which would otherwise have gone to stakers/bribe recipients as `mWOM` rewards.

### Citations

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

**File:** wombat/SmartWomConvert.sol (L98-147)
```text
    function maxSwapAmount() public view returns (uint256) {
        uint256 womCash = IAsset(womAsset).cash();
        uint256 womLiability = IAsset(womAsset).liability();
        if (womCash >= womLiability)
            return 0;

        return (womLiability - womCash) * ratio / DENOMINATOR;
    }

    function currentRatio() public view returns (uint256) {
        address[] memory tokenPath = new address[](2);
        tokenPath[0] = mWom;
        tokenPath[1] = wom;
        
        address[] memory poolPath = new address[](1);
        poolPath[0] = womMWomPool;
    
        (uint256 amountOut, ) = IWombatRouter(router).getAmountOut(tokenPath, poolPath, 1e18);
        return amountOut * DENOMINATOR / 1e18;
    }

    /* ============ External Functions ============ */

    function convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, msg.sender, _mode);
    }

    function convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)
        external
        returns (uint256 obtainedmWomAmount)
    {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, _for, _mode);
    }

    // should mainly used by wombat staking upon sending wom
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

**File:** wombat/SmartWomConvert.sol (L186-197)
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
```

**File:** wombat/WombatPoolHelper.sol (L142-144)
```text
    function harvest() external override {
        IWombatStaking(wombatStaking).harvest(lpToken);
    }
```
