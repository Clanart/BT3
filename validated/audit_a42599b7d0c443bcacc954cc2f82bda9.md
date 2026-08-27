### Title
Wombat pool deposit/withdraw and swap operations use `block.timestamp` as the AMM deadline, nullifying deadline protection - ([File: wombat/WombatStaking.sol])

### Summary
`WombatStaking.deposit` and `WombatStaking.withdraw`, which are reachable by any unprivileged wallet via the pool helper contracts (`WombatPoolHelper`, `WombatPoolHelperV2`, `AnkrBNBPoolHelper`), pass `block.timestamp` as the `deadline` parameter to `IWombatPool.deposit`/`IWombatPool.withdraw`. The same pattern appears in `SmartWomConvert._convertFor` (passing `block.timestamp` to `IWombatRouter.swapExactTokensForTokens`) and in `ArbWomUp2._bullMGP`/`ArbWomUp3._bullMGP` when swapping via PancakeRouter/TraderJoe LBRouter.

### Finding Description
The Wombat pool's `deposit`/`withdraw` functions accept a `deadline` argument specifically to protect users from transactions being withheld by a miner/validator/sequencer and executed later at a worse price. However, `WombatStaking.deposit` and `WombatStaking.withdraw` compute this deadline as `block.timestamp` at execution time rather than a user-supplied, pre-agreed deadline: [1](#0-0) [2](#0-1) 

Because `block.timestamp` is evaluated inside the pool contract at the moment of mining, the check `deadline >= block.timestamp` (enforced in the AMM/pool contract) is always trivially true no matter when the transaction is actually included. This means a validator/sequencer (or any actor who can delay inclusion, e.g., via private mempools/MEV) can hold a user's deposit/withdraw transaction indefinitely and choose to include it at the most advantageous moment for themselves (e.g., after adverse price movement in the Wombat stable pool), only bounded by the user-supplied `_minimumLiquidity`/`_minAmount` slippage parameter.

The same defect exists in `SmartWomConvert._convertFor`, where the WOM→mWOM buyback swap through `IWombatRouter.swapExactTokensForTokens` uses `block.timestamp` as the deadline: [3](#0-2) 

None of these entry points (`WombatPoolHelper.deposit`/`withdraw`, `WombatStaking.deposit`/`withdraw`, `SmartWomConvert.convert`/`convertFor`/`smartConvert`) let the caller supply their own deadline, so there is no way for a user to actually bound how long their transaction can be withheld before execution.

### Impact Explanation
An attacker with transaction-ordering power (miner/validator/searcher with private order flow) can delay a user's deposit/withdraw/convert transaction and execute it once conditions (e.g., pool imbalance, WOM/mWOM ratio) are maximally unfavorable to the user, up to the limit of the user's slippage tolerance (`_minimumLiquidity` / `_minAmount` / `_minRec`). This does not guarantee unbounded loss (bounded by the slippage parameter), but it does allow adversarial timing/MEV extraction against LP depositors and withdrawers, and it removes the intended protection the deadline parameter is designed to provide.

### Likelihood Explanation
Likelihood is moderate: it requires an actor with the ability to delay/order transaction inclusion (validator, sequencer, or MEV searcher with mempool visibility) and requires the withheld transaction to remain valid and profitable to delay, which is realistic on public chains especially during periods of pool imbalance or volatility.

### Recommendation
Add a `deadline` parameter to `WombatStaking.deposit`, `WombatStaking.withdraw`, the corresponding `PoolHelper` functions, and `SmartWomConvert.convert`/`convertFor`/`smartConvert`, allowing the calling user to specify their own deadline, and forward that user-supplied value to `IWombatPool.deposit`/`withdraw` and `IWombatRouter.swapExactTokensForTokens` instead of computing `block.timestamp` inside the transaction.

### Proof of Concept
1. A user calls `WombatPoolHelper.withdraw(_liquidity, _minAmount)` to withdraw stables from the Wombat pool.
2. This routes to `WombatStaking.withdraw`, which calls `IWombatPool(poolInfo.depositTarget).withdraw(poolInfo.depositToken, _liquidity, _minAmount, address(this), block.timestamp)` [2](#0-1) .
3. A validator/sequencer observes this transaction in the mempool and withholds it while the Wombat pool's asset coverage ratio worsens (e.g., after a large imbalance swap by another actor).
4. The validator later includes the withdraw transaction once the price impact is at its worst point still satisfying `_minAmount`, extracting value that would not have occurred had a genuine, user-set deadline forced timely inclusion.

### Citations

**File:** wombat/WombatStaking.sol (L256-263)
```text
        IWombatPool(poolInfo.depositTarget).deposit(
            depositToken,
            _amount,
            _minimumLiquidity,
            address(this),
            block.timestamp,
            false
        );
```

**File:** wombat/WombatStaking.sol (L307-313)
```text
        IWombatPool(poolInfo.depositTarget).withdraw(
            poolInfo.depositToken,
            _liquidity,
            _minAmount,
            address(this),
            block.timestamp
        );
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
