## Title
Missing slippage protection in `ArbWomUp3.incentiveDeposit` (mode 2) exposes users to sandwich attacks on the WOM→mWom swap - (File: `wombat/ArbWomUp3.sol`)

### Summary
`ArbWomUp3.incentiveDeposit()` lets any wallet deposit WOM in exchange for locked mWom and bonus MGP rewards. When called with `_mode == 2`, half of the deposited WOM is routed through `SmartWomConvert.convert()`, which performs an on-chain swap via the Wombat router. `ArbWomUp3._deposit` hardcodes the `_minRec` argument to `0`, and `SmartWomConvert._convertFor` in turn calls `swapExactTokensForTokens` with an `amountOutMin` of `0`, disabling any protection against price movement between submission and execution — the exact bug class described in the external report (no way for the user to specify a minimum acceptable output/slippage bound).

### Finding Description
`incentiveDeposit` has no parameter allowing the caller to set a minimum receive amount: [1](#0-0) 

For `_mode == 2`, `_deposit` splits the user's WOM and forwards half to `SmartWomConvert.convert` with `_minRec` hardcoded to `0`: [2](#0-1) 

Inside `SmartWomConvert._convertFor`, the buyback portion is swapped through the Wombat router with `amountOutMin` hardcoded to `0`, and the only downstream guard (`convertAmount + amountRec < _minRec`) is neutralized because `_minRec` is `0`: [3](#0-2) 

Because both the on-chain swap call and the post-swap sanity check use a zero minimum, an attacker (or ordinary MEV searcher) can sandwich the user's `incentiveDeposit` transaction, manipulating the `wom`/`mWom` pool price immediately before the swap executes and reverting it afterward, so the user's WOM is swapped for far less mWom than fair value. This directly mirrors the reported `spot_lp` bug class: the function computes/executes at the current pool state with no user-specified minimum-output or maximum-slippage bound.

### Impact Explanation
The swapped portion of a user's WOM deposit can be converted at an arbitrarily bad rate, resulting in direct, quantifiable loss of the depositing user's own funds captured by the sandwiching party. Since `incentiveDeposit` is a completely public, unprivileged entry point reachable by any wallet, this is a direct theft-of-user-funds vector, not a privileged/admin issue.

### Likelihood Explanation
Any transaction to `incentiveDeposit` with `_mode == 2` is trivially detectable in the mempool and sandwichable, since the amountOutMin is unconditionally `0`. No special permissions or preconditions are required beyond a non-zero deposit amount, making this readily and repeatedly exploitable by MEV bots.

### Recommendation
Add a `_minRec`/slippage parameter to `incentiveDeposit` (and thread it through `_deposit`) so users can specify their own acceptable minimum mWom output for the mode-2 swap path, and pass that value into `SmartWomConvert.convert`/`_convertFor` instead of hardcoding `0`. Additionally, `SmartWomConvert._convertFor` should pass a non-zero `amountOutMin` (derived from `_minRec`) directly into `swapExactTokensForTokens` rather than relying solely on a post-hoc balance check.

### Proof of Concept
1. User calls `incentiveDeposit(amount, convertRatio, false, 2)`.
2. `_deposit` computes `toSwap = amount - amount/2` and calls `smartWomConvert.convert(toSwap, convertRatio, 0, 0)`.
3. Attacker front-runs with a large swap in the same `womMWomPool` direction, pushing the pool price against the pending swap; `SmartWomConvert._convertFor` executes `swapExactTokensForTokens(..., 0, ...)` at the manipulated price, receiving significantly less `mWom`; attacker back-runs to restore price and pocket the difference.
4. The check `convertAmount + amountRec < _minRec` never triggers because `_minRec == 0`, so the transaction succeeds despite the unfavorable rate, and the user's locked mWom position is smaller than it should be.

### Citations

**File:** wombat/ArbWomUp3.sol (L88-99)
```text
    function incentiveDeposit(
        uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode // 1 stake, 2 lock
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        if (_amount == 0) return;
        
        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender, _mode == 2);

        // giving out 50% more bonus
        if (_mode == 2)
            rewardToSend = rewardToSend * 2;

        _deposit(msg.sender, _convertRatio, _amount, _mode);
```

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
