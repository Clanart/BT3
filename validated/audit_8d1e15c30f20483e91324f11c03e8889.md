### Title
Excess native ETH sent to `exactOutputSingle` / `exactOutput` is permanently stranded on the router and stealable by any caller via `refundETH()` — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol` / `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`exactOutputSingle` and `exactOutput` are `payable` and accept native ETH as a substitute for WETH input. The internal `pay()` helper deposits only the exact amount the pool requests (`amountIn`), leaving any `msg.value` surplus on the router. No automatic refund is issued. Because `refundETH()` is a public function that sweeps the entire router ETH balance to `msg.sender`, any third party can immediately steal the stranded surplus in a follow-up call.

---

### Finding Description

`PeripheryPayments.pay()` handles WETH-input swaps with native ETH via the following branch:

```solidity
// PeripheryPayments.sol lines 73-84
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();          // deposits exactly `value`
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

When `nativeBalance >= value`, the function deposits exactly `value` ETH and leaves `nativeBalance - value` on the router with no refund path.

`exactOutputSingle` calls `pay()` with `value = actual amountIn` (determined by the pool at swap time):

```solidity
// MetricOmmSimpleRouter.sol lines 130-147
function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    ...
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    ...
    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
    // ← no refund of msg.value surplus
}
```

The function returns `amountIn` but never refunds `msg.value - amountIn`. The same omission exists in `exactOutput` (lines 154–188).

`refundETH()` is unconditionally public:

```solidity
// PeripheryPayments.sol lines 58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // sends to caller, not original depositor
    }
}
```

Any address that calls `refundETH()` after Alice's `exactOutputSingle` transaction receives Alice's stranded ETH.

The same root cause applies to `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` and `addLiquidityWeighted`, which are also `payable` and settle via the same `pay()` function without automatic refund.

---

### Impact Explanation

A user who calls `exactOutputSingle{value: X}` with WETH as `tokenIn` and `X > actual amountIn` loses `X - amountIn` ETH. The surplus is immediately claimable by any address via `refundETH()`. This is a direct, unprivileged theft of user principal with no recovery path once the transaction is mined. The loss scales with the gap between `amountInMaximum` (what the user sends) and the realized `amountIn` (what the pool charges), which is non-zero in virtually every exact-output swap.

**Severity: High** — direct loss of user ETH, no privilege required, no time window needed.

---

### Likelihood Explanation

The standard UX pattern for exact-output swaps is to send `msg.value = amountInMaximum` (or a quoted upper bound) because the caller cannot know the exact input before the swap executes. The Uniswap v3 periphery resolves this by appending `refundETH()` inside a `multicall`, but that requires the caller to know the pattern. A direct call to `exactOutputSingle` with any surplus ETH — which is the natural usage — silently strands funds. The `refundETH()` theft vector requires only a mempool watcher and a single follow-up call, making it trivially exploitable.

---

### Recommendation

Add an automatic ETH refund at the end of `exactOutputSingle` and `exactOutput` (and the liquidity adder equivalents) when `tokenIn == WETH`:

```solidity
// After _clearExpectedCallbackPool():
uint256 surplus = address(this).balance;
if (surplus > 0) {
    _transferETH(msg.sender, surplus);
}
```

Alternatively, document that callers **must** wrap these calls in `multicall([exactOutputSingle(...), refundETH()])` and enforce it by making the functions non-`payable` when called outside `multicall`. The safest fix is the automatic refund, mirroring the resolution applied to the analogous `Polygon_SpokePool` issue.

---

### Proof of Concept

```
1. Alice wants to buy exactly 1,500 token1 for WETH.
2. She calls:
       router.exactOutputSingle{value: 2 ether}(ExactOutputSingleParams({
           pool: pool,
           tokenIn: WETH,
           tokenOut: token1,
           zeroForOne: true,
           amountOut: 1_500,
           amountInMaximum: 2 ether,   // upper bound; actual cost ~1 ether
           recipient: alice,
           ...
       }));
3. Inside the swap callback, pay() is called with value = ~1 ether (actual amountIn).
   nativeBalance = 2 ether >= 1 ether → deposits exactly 1 ether, transfers WETH to pool.
   Remaining 1 ether stays on the router.
4. exactOutputSingle returns. No refund is issued.
5. Bob (mempool watcher) calls router.refundETH() in the next block.
   refundETH() sends address(router).balance (= 1 ether) to Bob.
6. Alice loses 1 ether; Bob gains 1 ether. Alice received her 1,500 token1 but paid 2 ether instead of ~1 ether.
```