I have enough evidence to confirm the finding in `ArbWomUp3.sol`. The `incentiveDeposit` function is directly callable by any wallet, and its internal `_deposit` for `_mode == 2` passes a hardcoded `0` as `_minRec` to `SmartWomConvert.convert`, which performs an on-chain swap via `IWombatRouter.swapExactTokensForTokens` with no minimum-output protection.

### Title
Missing slippage protection in `ArbWomUp3.incentiveDeposit` (mode 2) exposes users to sandwich attacks during WOM→mWOM conversion - ([File: wombat/ArbWomUp3.sol])

### Summary
`ArbWomUp3.incentiveDeposit` is a public, unprivileged entrypoint that lets any wallet deposit WOM in exchange for MGP incentive rewards. When `_mode == 2`, half of the deposited WOM is routed through `SmartWomConvert.convert`, which internally swaps WOM for mWOM via the Wombat router. `ArbWomUp3` hardcodes the `_minRec` slippage parameter to `0`, disabling the only protection meant to guard this swap.

### Finding Description
`incentiveDeposit` (external, no access control besides pause) calls `_deposit(msg.sender, _convertRatio, _amount, _mode)`: [1](#0-0) 

For `_mode == 2`, `_deposit` swaps a portion of WOM to mWOM by calling `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)`, passing `0` for `_minRec`: [2](#0-1) 

`SmartWomConvert.convert` forwards to `_convertFor`, which uses that `_minRec` to guard both the router swap output and the final check `convertAmount + amountRec < _minRec`: [3](#0-2) 

Because `_minRec` is `0`, the router call `IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)` accepts any output amount, and the subsequent revert check is vacuous (`x < 0` is never true). The resulting `mWomBal` is then locked entirely into `mWomSV` on behalf of the depositing user: [4](#0-3) 

This is functionally the same bug class as the reported issue: a user-facing function that performs an economically consequential exchange (WOM → mWOM) with no way to bound the acceptable output, letting execution-time price movement (or intentional manipulation) reduce what the user actually receives and gets locked, with no recourse.

### Impact Explanation
An attacker who observes a pending `incentiveDeposit(_mode == 2)` transaction in the mempool can sandwich the swap on the `womMWomPool` (front-run to worsen the WOM/mWOM exchange rate, let the victim's swap execute at the manipulated rate, then back-run to restore/profit). Since `_minRec` is `0`, the victim's swap will succeed regardless of how unfavorable the resulting `amountRec` is. The victim's WOM is irrevocably transferred and permanently locked as a reduced amount of mWOM in `mWomSV`, constituting a direct, uncompensated loss of user funds extracted by the attacker.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires a sandwich attacker monitoring the mempool and sufficient buyback-pool depth/slippage on `womMWomPool` for the attack to be profitable net of gas and swap fees, and only affects users who choose `_mode == 2`. However, the function is entirely unprivileged, always reachable, and the vulnerable code path is unconditionally exercised whenever `buybackAmount > 0` and `_mode == 2` is selected — there is no user-side control at all to reduce exposure.

### Recommendation
Add a `_minRec` (or `minMWomOut`) parameter to `ArbWomUp3.incentiveDeposit` / `_deposit`, allow the caller to specify their own minimum acceptable output, and forward that value (instead of the hardcoded `0`) into `IConverter(smartWomConvert).convert(...)`. Ensure the check in `SmartWomConvert._convertFor` (`convertAmount + amountRec < _minRec`) is meaningful (i.e., not silently defeated by a caller-supplied `0` propagated from an upstream contract), and consider emitting a revert if `_minRec` is not explicitly supplied.

### Proof of Concept
1. Attacker monitors mempool for a pending `ArbWomUp3.incentiveDeposit(_amount, _convertRatio, _bullMode, 2)` transaction from a victim.
2. Attacker front-runs by swapping WOM → mWOM on the same `womMWomPool` used by `SmartWomConvert`, pushing the mWOM price up (WOM/mWOM ratio worsens for subsequent swappers).
3. Victim's transaction executes: `_deposit` calls `smartWomConvert.convert(toSwap, _convertRatio, 0, 0)`, which swaps `buybackAmount` WOM for mWOM via `IWombatRouter.swapExactTokensForTokens(..., 0, ...)` — accepting the now-worse `amountRec`.
4. `mWomBal` (deposit amount + reduced `amountRec`) is locked into `mWomSV` for the victim via `ILocker(mWomSV).lockFor(mWomBal, _account)`, permanently fixing the victim's loss.
5. Attacker back-runs by swapping mWOM → WOM to realize the price difference as profit, net of pool/router fees. [2](#0-1) [3](#0-2)

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
