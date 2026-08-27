### Title
Unprotected buyback swap (minAmountOut = 0) + caller-controlled convertRatio allows price manipulation to extract excess mWOM from `womMWomPool` - ([File: wombat/SmartWomConvert.sol])

### Summary
`_convertFor` executes the WOM→mWOM buyback leg via `IWombatRouter.swapExactTokensForTokens` with a hardcoded `minAmountOut` of `0`, and the only downstream check (`convertAmount + amountRec < _minRec`) enforces a 1:1 floor, not a fair-price ceiling. Combined with `convert()`/`convertFor()` letting any unprivileged caller pick an arbitrary `_convertRatio`, or with `smartConvert()`'s reliance on the instantaneously-read `currentRatio()`/`maxSwapAmount()` (both spot-computed from the live pool state, with no TWAP), an attacker can atomically depress the mWOM/WOM price on `womMWomPool` and then have their own `convert`/`smartConvert` call buy mWOM at that manipulated, artificially favorable price in the same transaction.

### Finding Description
- `currentRatio()` (`wombat/SmartWomConvert.sol:107-117`) and `maxSwapAmount()` (`wombat/SmartWomConvert.sol:98-105`) are both spot reads: `currentRatio()` quotes the router for a 1e18 mWOM→WOM swap against the live pool reserves, and `maxSwapAmount()` derives the cap purely from `IAsset(womAsset).cash()`/`liability()`, independent of price. Neither uses a TWAP or any manipulation-resistant oracle.
- In `smartConvert()` (`wombat/SmartWomConvert.sol:133-147`), whether the "buyback" (swap-based) path triggers, and how large `amountToSwap` can be, is decided from these spot values computed in the same call.
- Crucially, `convert()`/`convertFor()` (`wombat/SmartWomConvert.sol:121-130`) bypass this threshold logic entirely: any unprivileged caller supplies `_convertRatio` directly, so they can force `buybackAmount = _amount` (100% buyback) at will, with no threshold check at all.
- The actual swap in `_convertFor` (`wombat/SmartWomConvert.sol:186-197`) is:
```
amountRec = IWombatRouter(router).swapExactTokensForTokens(
    tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp
);
```
`minAmountOut` is hardcoded to `0` — there is no protection tying the executed swap price to a fair/expected price.
- The only sanity check afterward, `convertAmount + amountRec < _minRec` (`wombat/SmartWomConvert.sol:204-205`), only guarantees the caller doesn't receive **less** than `_amountIn` in token-count terms; it never bounds the **upper** side, so it does nothing to stop the caller from extracting a favorable (manipulated) price.
- Exploit flow: an attacker sells (flash-loaned or otherwise sourced) mWOM into `womMWomPool` to depress the mWOM/WOM price within the same block/transaction, then calls `convert(_amountIn, 0, 0, mode)` (or, if forcing through `smartConvert`, first depresses `currentRatio()` below `buybackThreshold`) so the buyback swap executes against the just-manipulated, discounted pool state, receiving more mWOM per WOM spent than the true/steady-state price would allow. The attacker repays any flash loan from the proceeds and keeps the excess mWOM.
- Existing protections do not stop this: there is no `nonReentrant` issue at play, no pause blocks it, and the receipt/floor check (`_minRec`) is a lower bound only, not an upper bound tied to a manipulation-resistant price.

### Impact Explanation
The excess mWOM the attacker extracts is paid out of `womMWomPool`'s mWOM reserves at a price worse than fair value, i.e., value is transferred from the pool's liquidity (and, if the WOM-side inventory or the pool is protocol-owned/protocol-linked, from protocol backing) to the attacker. This matches an Immunefi "protocol insolvency" / "direct theft of funds" style impact: the mWOM peg backing degrades because mWOM is handed out at a rate more favorable than the pool's true equilibrium price, funded by the pool's or protocol's reserves rather than by the attacker's deposited WOM value.

### Likelihood Explanation
- Fully attacker-reachable: `convert()`/`convertFor()` and `smartConvert()` are `external`, callable by any EOA/contract holding WOM (and some source of mWOM to skew the pool), with no privileged role required.
- Requires only same-block/flash-loan capital to move `womMWomPool`'s reserves and sufficient own WOM capital to size the buyback leg (bounded further by `maxSwapAmount()`, which scales with `womAsset.cash()`/`liability()` imbalance — an attacker-influenced/flash-loan-affected metric as well).
- Repeatable each time the pool has enough depth/imbalance headroom to be profitably skewed, i.e., not a one-off condition.
- The main uncertainty is quantifying net profitability after AMM fees/curve slippage on both legs (the "skew" trade and the "buyback" trade) for the specific Wombat stableswap curve parameters — this would need to be validated numerically/empirically against the deployed pool's actual `A`/fee parameters, which are not available in this file set.

### Recommendation
- Do not hardcode `minAmountOut = 0` in the router swap in `_convertFor`; require callers (or `smartConvert`'s internal logic) to pass a real minimum output derived from a manipulation-resistant reference price (e.g., a TWAP of `womMWomPool`, or a bound relative to `currentRatio()` sampled over multiple blocks).
- Add an upper-bound sanity check on `amountRec` relative to a fair-value estimate (e.g., reject swaps producing more than a small premium over the pool's TWAP-implied output) to prevent extraction of artificially favorable prices.
- For `smartConvert()`, use a TWAP-based `currentRatio()` (or require the threshold check and swap to be computed from reserves sampled at a delayed/oracle-protected snapshot) instead of a spot quote from the router, and consider disallowing/limiting `convert()`/`convertFor()`'s caller-supplied `_convertRatio` when it triggers large buyback amounts without equivalent protection.

### Proof of Concept
Foundry fork test plan:
1. Fork mainnet/target chain at a block where `womMWomPool` has meaningful liquidity.
2. Deploy an attacker contract that:
   a. Flash-loans (or otherwise sources) mWOM.
   b. Swaps mWOM → WOM directly on `womMWomPool` via the Wombat router to depress `currentRatio()`.
   c. In the same transaction, calls `SmartWomConvert.convert(_amountIn, 0, 0, 0)` (forcing 100% buyback) using attacker-owned WOM.
   d. Repays the flash loan from the WOM/mWOM proceeds.
3. Assert: `obtainedmWomAmount` (in WOM-equivalent value, valued at the pre-manipulation/TWAP price) exceeds `_amountIn`, i.e., the attacker's net WOM-value position increases without providing equivalent WOM value — demonstrating value extraction from the pool.
4. Compare against a control run without step (b)'s manipulation to show the manipulation step is what produces the profit delta.