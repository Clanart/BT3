### Title
Zero-slippage swap in `ArbWomUp3.incentiveDeposit` (mode 2) allows sandwich attack to steal user funds - (File: wombat/ArbWomUp3.sol)

### Summary
`ArbWomUp3.incentiveDeposit` (an unprivileged, user-callable airdrop/incentive-deposit function) allows any wallet to deposit WOM, half of which is routed through `SmartWomConvert.convert()` with the minimum-received (`_minRec`) parameter hardcoded to `0`. This removes all slippage protection on the underlying AMM swap, letting a keeper/MEV bot sandwich the transaction and steal value from the depositor's swapped WOM before it is locked into `mWomSV`.

### Finding Description
`ArbWomUp3.incentiveDeposit` is external and callable by any wallet (only gated by `_checkAmount`, `whenNotPaused`, `nonReentrant`): [1](#0-0) 

For `_mode == 2`, half of the deposited WOM is swapped via `SmartWomConvert.convert()` with `_minRec` hardcoded to `0`: [2](#0-1) 

Inside `SmartWomConvert.convert()` → `_convertFor()`, the buyback leg performs an on-chain WOM→mWOM swap through the Wombat router with the swap's own `minAmountOut` also hardcoded to `0`, and the only downstream safety check compares the combined output against the caller-supplied `_minRec`: [3](#0-2) 

Because `ArbWomUp3` passes `_minRec = 0`, the final `convertAmount + amountRec < _minRec` check can never revert regardless of how badly the swap price is manipulated. The caller of `incentiveDeposit` has no parameter to specify their own acceptable slippage — the function signature only exposes `_convertRatio`, not a minimum-output guard: [4](#0-3) 

This is the same root cause identified in the referenced report ([TopUpAction.sol#L154](https://github.com/code-423n4/2022-04-backd/blob/c856714a50437cb33240a5964b63687c9876275b/backd/contracts/actions/topup/TopUpAction.sol#L154)): a swap executed on behalf of an ordinary user with no user-configurable or otherwise meaningful minimum-output protection, enabling sandwich extraction of value that belongs to the depositor.

### Impact Explanation
Any unprivileged wallet calling `incentiveDeposit(_amount, _convertRatio, _bullMode, 2)` has ~50% of their deposited WOM routed through an AMM swap with zero slippage protection. An MEV bot/keeper watching the mempool can sandwich this swap (buy mWOM before, sell after) to push the received `mWOM` amount arbitrarily low, directly reducing the amount of `mWOM` ultimately locked into `mWomSV` on the depositor's behalf. This is a direct, deterministic theft of user funds during a routine, unprivileged user transaction — the victim cannot opt out or protect themselves since no slippage parameter is exposed at the `ArbWomUp3` layer.

### Likelihood Explanation
High. `incentiveDeposit` is a normal, permissionless entry point intended for regular users participating in the incentive/airdrop program, and every `_mode == 2` call triggers the unprotected swap. Sandwiching an unprotected on-chain swap is a well-understood, low-cost, and repeatable MEV strategy requiring no special access.

### Recommendation
Add a user-supplied minimum-received parameter to `incentiveDeposit` (and thread it through to the `SmartWomConvert.convert()` call instead of hardcoding `0`), similar to how `_minAmount`/`_minimumLiquidity` are already exposed as caller-controlled parameters in `WombatStaking.deposit`/`withdraw` and the pool helpers. Alternatively, compute an off-chain-quoted expected output with an acceptable tolerance and pass it as `_minRec`.

### Proof of Concept
1. Attacker monitors the mempool for `ArbWomUp3.incentiveDeposit(amount, convertRatio, false, 2)` transactions.
2. Attacker front-runs with a large WOM→mWOM swap on the `womMWomPool`, moving the price unfavorably for the victim.
3. Victim's transaction executes: `_deposit` swaps `toSwap` WOM via `SmartWomConvert.convert(toSwap, _convertRatio, 0, 0)` — since `_minRec = 0`, the swap executes at the manipulated price with no revert protection [5](#0-4) .
4. Attacker back-runs, reversing their initial swap and pocketing the difference, which comes at the expense of the reduced `mWOM` amount that would have been locked for the victim in `mWomSV`.

### Citations

**File:** wombat/ArbWomUp3.sol (L88-105)
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

        IERC20(mgp).safeApprove(address(vlMGP), rewardToSend);
        vlMGP.lockFor(rewardToSend, msg.sender);
        // _bullMGP(rewardToSend, _minMGPRec, msg.sender);
        emit VLMGPRewarded(msg.sender, 0, rewardToSend);
    }
```

**File:** wombat/ArbWomUp3.sol (L189-204)
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
