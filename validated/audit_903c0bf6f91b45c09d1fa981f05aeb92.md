### Title
Unrestricted `harvest()`/fee-conversion path lets anyone manipulate the WOM/mWom pool price to skew protocol fee conversion — analogous to the "too open rebalance" issue in `RSIManager.sol` - (File: `wombat/WombatStaking.sol`, `wombat/SmartWomConvert.sol`)

### Summary
`WombatStaking.harvest()` can be called by any unprivileged wallet and internally triggers `_sendRewards()` → `SmartWomConvert.smartConvert()`, a "rebalance"-style conversion whose behavior (whether/how much WOM is bought back into mWOM) is derived entirely from the *live, unprotected spot price* of the `womMWomPool` AMM. Because the triggering function is open to any caller and the price source has no TWAP/oracle protection, an attacker can manipulate the pool price immediately before calling `harvest()` to force an unfavorable conversion of protocol-held WOM rewards, exactly the "too open rebalance" pattern flagged in the external `RSIManager.sol` report (an externally-callable rebalancing function with no caller restriction that is vulnerable to market manipulation).

### Finding Description
`harvest()` has no caller restriction beyond the pool being active: [1](#0-0) 

It calls `_toMasterWomAndSendReward` → `_sendRewards`, which for WOM rewards routes a fee slice through `smartWomConverter.smartConvert(feeAmount, 0)`: [2](#0-1) 

`smartConvert()` decides how much of the protocol's harvested WOM to swap for mWOM vs. simply mint 1:1, based entirely on `currentRatio()` and `maxSwapAmount()`, both of which read live, unprotected AMM state from `womMWomPool`/`womAsset`: [3](#0-2) [4](#0-3) 

The actual swap executed inside `_convertFor` passes `0` as the router's minimum-out parameter, relying solely on an aggregate `_minRec` check afterward: [5](#0-4) 

Since `currentRatio()`/`maxSwapAmount()` are read from the same AMM pool that is about to be swapped against, and `harvest()` is callable by anyone with no restriction (unlike deposits/withdrawals which are gated by `_onlyPoolHelper`/`_onlyActivePoolHelper`), an ordinary wallet can, in a single transaction: (1) swap against `womMWomPool` to distort the wom/mWOM price, (2) call `harvest()` on any active pool to force `smartConvert()` to make its buyback/mint decision using the distorted price, and (3) reverse the initial swap — extracting value from the protocol's own harvested WOM yield that would otherwise have flowed to stakers as mWOM/rewards. This mirrors the reported `RSIManager.sol` pattern: an externally-callable function that performs a market-sensitive action (rebalance/convert) with no restriction on who can trigger it, making it directly exploitable for market manipulation.

### Impact Explanation
Because `harvest()` is invoked as part of essentially every deposit/withdraw/harvest flow and is permissionless, an attacker can repeatedly time the price manipulation to bias the fee-conversion ratio in their favor whenever fees are large enough to be worth the attack, degrading the value of protocol/staker yield converted into mWOM. This constitutes theft/degradation of unclaimed yield belonging to WOM stakers/voters, since the "buyback vs mint" decision — meant to protect the mWOM peg for stakers' benefit — is computed from manipulable spot data at a time chosen by the attacker.

### Likelihood Explanation
`harvest()` has no access control (only `whenNotPaused` and `_onlyActivePool`), so any wallet can call it at will, and `womMWomPool` is a standard AMM whose spot price can be moved within a single transaction/flash loan. No privileged role or governance action is required.

### Recommendation
Restrict `harvest()`'s fee-conversion path (or `smartConvert`) to rely on a manipulation-resistant price reference (e.g., TWAP, or a governance/keeper-restricted caller with pre/post price-impact checks), and/or add a minimum-out check on the internal router swap in `SmartWomConvert._convertFor` rather than deferring solely to the aggregate `_minRec`. Consider gating `harvest()`/`smartConvert()` fee routing to a trusted keeper or adding slippage bounds tied to `currentRatio()` computed over a longer window.

### Proof of Concept
1. Attacker flash-swaps a large amount through `womMWomPool` to push `currentRatio()` below `buybackThreshold` (or otherwise skew it) in the same transaction.
2. Attacker calls `WombatStaking.harvest(_lpToken)` (unrestricted), which internally invokes `smartWomConverter.smartConvert(feeAmount, 0)` using the now-manipulated `currentRatio()`/`maxSwapAmount()` to decide the buyback amount and execute a swap with `0` minimum-out inside `_convertFor`.
3. Attacker reverses the initial flash-swap, having captured the price-impact spread at the expense of the protocol's harvested WOM fee destined for stakers.

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

**File:** wombat/WombatStaking.sol (L739-752)
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
```

**File:** wombat/SmartWomConvert.sol (L98-117)
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

**File:** wombat/SmartWomConvert.sol (L186-205)
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
```
