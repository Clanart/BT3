### Title
Caller-controlled `_convertRatio` in `ArbWomUp3._deposit` mode 2 lets depositors bypass protocol-computed buyback sizing and extract value from the shared wom/mWom pool - (File: wombat/ArbWomUp3.sol)

### Summary
`incentiveDeposit` (mode 2) calls `_deposit`, which forwards the caller-supplied `_convertRatio` directly into `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)` [1](#0-0) . This bypasses `SmartWomConvert.smartConvert()`, the intended path that computes the buyback ratio from `currentRatio()`/`buybackThreshold`/`maxSwapAmount()` to protect the shared `womMWomPool` [2](#0-1) . Because `convert()`/`_convertFor()` accept any caller-chosen ratio with `_minRec = 0` and apply no cap tied to `maxSwapAmount()`, a depositor can always pick the ratio that maximizes their own mWom yield relative to the direct 1:1 mint path, extracting value from the pool whenever price conditions are favorable, and defaulting to the safe 1:1 mint path (ratio = 10000) otherwise.

### Finding Description
`_deposit` mode 2 splits the deposited `wom` in half: one half is minted 1:1 into `mWom` via `IMWom(mWom).deposit`, and the other half (`toSwap`) is routed through `smartWomConvert.convert(toSwap, _convertRatio, 0, 0)` using the caller's own `_convertRatio` [1](#0-0) . Inside `SmartWomConvert._convertFor`, `_convertRatio` determines the split between `buybackAmount` (swapped through the AMM router against `womMWomPool`) and `convertAmount` (minted 1:1) [3](#0-2) . The safe entrypoint `smartConvert()` computes this ratio itself from `currentRatio()` and `maxSwapAmount()`, capping how much can be swapped through the pool based on real pool imbalance and a `buybackThreshold` [2](#0-1) [4](#0-3) . `ArbWomUp3` instead calls the raw `convert()` entrypoint and lets an unprivileged caller choose the ratio directly, with `_minRec = 0`, meaning there is no floor on the outcome and no enforcement that `buybackAmount` stays within `maxSwapAmount()`. Since `_convertFor` performs `swapExactTokensForTokens` against `womMWomPool` for whatever `buybackAmount` the caller dictates, an attacker can inspect `SmartWomConvert.currentRatio()` off-chain before calling and choose `_convertRatio = 0` (maximal swap) whenever the AMM currently yields more than 1 mWom per wom, or `_convertRatio = 10000` (skip the swap entirely) whenever the AMM is unfavorable — a strictly dominant, riskless strategy not available to normal `smartConvert()` callers. The obtained `mWom` from this favorable swap is captured entirely via `IERC20(mWom).balanceOf(address(this))` and locked to the same caller in `mWomSV` [5](#0-4) , so any excess mWom extracted from the pool accrues directly to the attacker rather than being shared or capped by the protocol's designed buyback logic.

### Impact Explanation
This lets an unprivileged caller repeatedly extract value from the shared `womMWomPool` liquidity by choosing the swap-vs-mint ratio to their own advantage on every `incentiveDeposit(_amount, _convertRatio, _bullMode, 2)` call, with `_minRec = 0` removing any protocol-side floor. Each call is self-contained (the contract's mWom balance swept per transaction goes to the calling account), so the loss is not directly siphoned from other `ArbWomUp3` depositors' balances; the loss is borne by the `womMWomPool` (and by extension whichever party owns/backs that pool's liquidity), which the protocol's own `smartConvert()` path was explicitly designed to protect via `maxSwapAmount()`/`buybackThreshold`. This constitutes unrestricted, riskless value extraction from a shared liquidity venue enabled by improperly caller-supplied routing logic.

### Likelihood Explanation
Exploitation requires no privileged role: any EOA can call `incentiveDeposit` with `_mode = 2` and a self-chosen `_convertRatio`, after reading `SmartWomConvert.currentRatio()`/`estimateTotalConversion()` (both public view functions) to determine the optimal ratio. It is repeatable on every deposit and scales with the size of `toSwap`, bounded only by `IWombatRouter` swap price impact and the depositor's own wom balance/approval, requiring no flash loans or special capital beyond the deposit amount itself.

### Recommendation
Do not accept `_convertRatio` from the caller in `ArbWomUp3._deposit`. Route mode-2 deposits through `SmartWomConvert.smartConvert()` (which derives the ratio internally from `currentRatio()`/`maxSwapAmount()`/`buybackThreshold`) instead of calling `convert()` with a caller-supplied ratio, and/or enforce a non-zero `_minRec` computed from `estimateTotalConversion()` so the buyback leg can't be pushed to an attacker-chosen extreme independent of protocol-determined pool-health limits.

### Proof of Concept
1. Deploy mocked `wom`, `mWom`, `IWombatRouter`, `IAsset` (womAsset) and a real/near-real `SmartWomConvert`, plus `ArbWomUp3` wired to them, with `mWomSV.getUserTotalLocked(attacker) == 0`.
2. Set the mock router/pool so that swapping `wom -> mWom` currently returns more than 1:1 (i.e., `currentRatio() < DENOMINATOR`, simulating mWom trading below par).
3. As an unprivileged attacker with no elevated role, call `incentiveDeposit(amount, 0, false, 2)` (i.e., `_convertRatio = 0`, forcing 100% of `toSwap` through the AMM buyback leg) and record the `mWom` locked into `mWomSV` for the attacker.
4. Repeat the same deposit amount but with `_convertRatio = 10000` (skip swap, pure 1:1 mint) and compare resulting locked `mWom`.
5. Assert that the attacker-optimal call (choosing whichever ratio is favorable given current pool state, determined via a view-only pre-call to `currentRatio()`) always yields at least as much `mWom` as the protocol's own `smartConvert()`-computed ratio would produce for the same deposit and pool state, demonstrating the caller can systematically extract more value than the protocol-intended routing formula allows, with `_minRec = 0` providing no floor and no `maxSwapAmount()` cap being enforced on the caller-chosen `_convertRatio` path.

### Citations

**File:** wombat/ArbWomUp3.sol (L189-203)
```text
        } else if (_mode == 2) {
            uint256 toDeposit = _amount / 2;
            uint256 toSwap = _amount - toDeposit;

            // 50% goes to deposit
            IERC20(wom).safeApprove(mWom, toDeposit);
            IMWom(mWom).deposit(toDeposit); 

            // 50% smart smart convert
            IERC20(wom).safeApprove(smartWomConvert, toSwap);
            IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0);

            uint256 mWomBal = IERC20(mWom).balanceOf(address(this));
            IERC20(mWom).safeApprove(address(mWomSV), mWomBal);
            ILocker(mWomSV).lockFor(mWomBal, _account);
```

**File:** wombat/SmartWomConvert.sol (L98-105)
```text
    function maxSwapAmount() public view returns (uint256) {
        uint256 womCash = IAsset(womAsset).cash();
        uint256 womLiability = IAsset(womAsset).liability();
        if (womCash >= womLiability)
            return 0;

        return (womLiability - womCash) * ratio / DENOMINATOR;
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

**File:** wombat/SmartWomConvert.sol (L175-207)
```text
    function _convertFor(uint256 _amount, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)
        internal returns (uint256 obtainedmWomAmount) {

        if (_convertRatio > DENOMINATOR)
            revert IncorrectRatio();

        IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        uint256 buybackAmount = _amount - (_amount * _convertRatio / DENOMINATOR);
        uint256 convertAmount = _amount - buybackAmount;
        uint256 amountRec = 0;

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
