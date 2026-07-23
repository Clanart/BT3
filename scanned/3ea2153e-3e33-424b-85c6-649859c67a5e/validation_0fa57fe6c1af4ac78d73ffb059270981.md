### Title
Router `pay()` Consumes Unattributed Native ETH to Settle Any Caller's WETH Swap, Enabling Theft of Stranded ETH — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

---

### Summary

The `pay()` function in `PeripheryPayments.sol` uses the router's entire native ETH balance to settle WETH swap callbacks without any per-caller attribution. Any ETH stranded on the router from a prior user's excess `msg.value` (e.g., a multicall that omits `refundETH`) is silently consumed by the next caller's WETH swap. The original depositor loses their ETH; the attacker receives output tokens without spending any WETH.

---

### Finding Description

`pay()` is called inside `_justPayCallback` during every WETH-input swap. When `token == WETH` and `payer != address(this)`, the function reads the router's raw native balance and uses it first, before pulling from the named payer:

```solidity
// PeripheryPayments.sol lines 73-84
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
}
``` [1](#0-0) 

There is no check that the ETH on the router was deposited by the current caller. `address(this).balance` is a shared, unattributed pool. Any ETH left on the router by any prior transaction is treated as freely available payment for whoever calls next.

ETH reaches the router legitimately via `msg.value` on any payable entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`, `addLiquidityExactShares`, etc.). The `receive()` guard only blocks *direct* ETH pushes from non-WETH addresses; it does not prevent ETH from being stranded by a user who sends excess `msg.value` and omits `refundETH`. [2](#0-1) 

The `refundETH` helper is the intended cleanup mechanism, but it is a separate, optional call that users must explicitly include in their multicall. Nothing in the router enforces its inclusion. [3](#0-2) 

---

### Impact Explanation

**Direct loss of user principal (ETH).** When the router's native balance covers the full swap amount (`nativeBalance >= value`), the named `payer` is never touched — the router pays entirely from its own balance. The attacker receives output tokens without spending any WETH or ETH of their own. The victim's stranded ETH is permanently consumed by the pool as WETH payment for a stranger's trade.

The `exactInputSingle` entry point sets `payer = msg.sender` and `tokenToPay = params.tokenIn`: [4](#0-3) 

Because `pay()` ignores the named payer whenever native ETH is available, the attacker does not need WETH approved or even held. The router's stranded ETH covers the entire callback payment.

---

### Likelihood Explanation

**Medium.** The precondition is ETH stranded on the router. This occurs whenever a user sends excess `msg.value` in a multicall and omits `refundETH`, which is a realistic and documented usage pattern (the test suite explicitly demonstrates the `multicall{value}` + `refundETH` idiom, implying users are expected to compose it manually). Integrators building on top of the router are especially likely to omit the cleanup step. Once ETH is stranded, any observer can drain it in the next block with a single `exactInputSingle` call targeting a WETH pool.

---

### Recommendation

Track the ETH that belongs to the current execution context rather than reading the raw contract balance. One approach: record `msg.value` in transient storage at each payable entry point and deduct from that tracked amount inside `pay()`, reverting if the tracked amount is exhausted. Alternatively, require callers to pre-wrap ETH to WETH before calling the router and remove the native-ETH branch from `pay()` entirely, relying solely on `safeTransferFrom`. Either change eliminates the unattributed-balance race.

---

### Proof of Concept

1. **Alice** calls `router.multicall{value: 2 ether}([exactInputSingle(WETH→token1, amountIn=1 ether, amountOutMinimum=0, ...)])` — she forgets to append `refundETH`. The swap consumes 1 ETH; 1 ETH remains on the router.

2. **Bob** (attacker, zero WETH balance, zero WETH allowance) calls `router.exactInputSingle(ExactInputSingleParams{pool: wethToken1Pool, tokenIn: WETH, amountIn: 1 ether, amountOutMinimum: 0, ...})`.

3. Inside `metricOmmSwapCallback`, `_justPayCallback` calls `pay(WETH, Bob, pool, 1 ether)`.

4. `address(this).balance == 1 ether >= 1 ether` → router wraps Alice's ETH into WETH and transfers it to the pool. `safeTransferFrom(Bob, ...)` is never called.

5. Bob receives token1 output. Alice's 1 ETH is gone. Bob spent nothing. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
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
