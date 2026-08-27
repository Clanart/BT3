## Analysis

The reported bug class (liquidator/user-supplied swap output lacking minimum-received protection, enabling MEV front-run/sandwich losses) has a valid, reachable analog in `ArbWomUp3.sol`.

### Title
Hardcoded zero slippage protection in WOM→mWom conversion allows sandwich attacks on depositors - (File: `wombat/ArbWomUp3.sol`)

### Summary
`ArbWomUp3.incentiveDeposit()` is a fully unprivileged, externally callable function that lets any wallet deposit WOM to receive vlMGP/mWomSV rewards. When called with `_mode == 2`, it routes half of the deposited WOM through `SmartWomConvert.convert()` to swap WOM for mWom via the `WombatRouter`. The internal call hardcodes the minimum-received parameter to `0`, exactly the missing-slippage-parameter pattern described in the external report.

### Finding Description
In `_deposit()`, the `_mode == 2` branch performs: [1](#0-0) 

The call `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)` passes `_minRec = 0` unconditionally, regardless of the size of `toSwap` or current pool conditions. Inside `SmartWomConvert`, this flows into `_convertFor`, where the buyback portion is swapped through `IWombatRouter.swapExactTokensForTokens(...)` with the router-level `minimumAmount` also fixed to `0`: [2](#0-1) 

Since `_minRec` passed up from `ArbWomUp3` is `0`, the guard `if (convertAmount + amountRec < _minRec) revert MinRecNotMatch();` in `SmartWomConvert._convertFor` can never trigger for this caller, effectively disabling slippage protection entirely for this code path: [3](#0-2) 

This is unlike every other reachable swap/redeem entry point in the codebase (e.g. `WombatPoolHelper.withdraw`, `WombatStaking.withdraw`, `SmartWomConvert.convert`/`convertFor` when called directly), which all correctly forward a caller-supplied `_minAmount`/`_minRec`: [4](#0-3) [5](#0-4) 

`ArbWomUp3` is the outlier: it strips away the user's ability to set a minimum-received bound before calling the router-backed conversion, exactly mirroring the DSC liquidator bug where the caller has no way to bound their output against price manipulation between transaction submission and execution.

### Impact Explanation
Any ordinary wallet calling `incentiveDeposit(_amount, _convertRatio, _bullMode, 2)` can be sandwiched: an attacker manipulates the WOM/mWom pool price immediately before the victim's transaction executes, causing `amountRec` from the swap to be minimized, then reverses the trade afterward. Because `_minRec` is hardcoded to `0`, the deposit function cannot revert regardless of how much value is lost in the swap. The resulting (reduced) `mWomBal` is then permanently locked into `mWomSV.lockFor(mWomBal, _account)`: [6](#0-5) 
This constitutes direct, permanent theft of user funds extracted via MEV sandwich, with no recourse for the victim since the locked/reduced mWom balance cannot be topped up after the fact.

### Likelihood Explanation
`incentiveDeposit` is a public, unprivileged entry point with no access control, callable by any wallet with WOM tokens; `_mode == 2` is a normal, documented usage path (per the function comment "1 stake, 2 lock"). Sandwiching AMM swaps that lack minimum-output protection is a well-known, cheap, and reliably executable MEV strategy, making this readily exploitable by any searcher monitoring the mempool.

### Recommendation
Add a caller-supplied `_minRec` parameter to `incentiveDeposit`/`_deposit` for the `_mode == 2` path and forward it (instead of hardcoding `0`) into `IConverter(smartWomConvert).convert(toSwap, _convertRatio, _minRec, 0)`, mirroring the pattern already used correctly elsewhere in the codebase (e.g., `WombatPoolHelper.withdraw`, `SmartWomConvert.convert`).

### Proof of Concept
1. Attacker observes a pending `incentiveDeposit(_amount, _convertRatio, false, 2)` transaction from a victim in the mempool.
2. Attacker front-runs with a large swap on the WOM/mWom Wombat pool via the `WombatRouter`, skewing the exchange rate unfavorably for the upcoming buyback swap.
3. Victim's transaction executes: `_deposit` calls `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)`, which cannot revert since `_minRec == 0`, so the victim receives a reduced `amountRec` of mWom.
4. Attacker back-runs, reversing their initial swap and capturing the spread.
5. The victim's reduced `mWomBal` is locked into `mWomSV` via `lockFor`, permanently realizing the loss with no ability to reclaim the sandwiched value. [1](#0-0)

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

**File:** wombat/SmartWomConvert.sol (L121-130)
```text
    function convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, msg.sender, _mode);
    }

    function convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)
        external
        returns (uint256 obtainedmWomAmount)
    {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, _for, _mode);
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

**File:** wombat/SmartWomConvert.sol (L199-207)
```text
        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();

        obtainedmWomAmount = convertAmount + amountRec;
```

**File:** wombat/WombatPoolHelper.sol (L123-140)
```text
    /// @notice withdraw stables from wombat pool, auto unstake from master Magpie
    /// @param _liquidity the amount of liquidity to withdraw
    function withdraw(uint256 _liquidity, uint256 _minAmount) external override {
        // we have to withdraw from wombat exchange to harvest reward to base rewarder
        IWombatStaking(wombatStaking).withdraw(
            lpToken,
            _liquidity,
            _minAmount,
            msg.sender
        );
        // then we unstake from master wombat to trigger reward distribution from basereward
        _unstake(_liquidity, msg.sender);
        //  last burn the staking token withdrawn from Master Magpie
        IWombatStaking(wombatStaking).burnReceiptToken(lpToken, _liquidity);


        emit NewWithdraw(msg.sender, _liquidity);
    }
```
