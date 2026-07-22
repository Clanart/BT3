### Title
Stuck ETH from prior transactions drained by any caller via `refundETH()` or silently consumed by subsequent WETH swaps via `pay()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.refundETH()` sends the router's entire native ETH balance to `msg.sender` with no check that the caller deposited that ETH. Separately, `pay()` uses `address(this).balance` — the whole contract balance, not just the current transaction's `msg.value` — when settling WETH swaps. When a user overpays for an `exactOutputSingle` or `exactInputSingle` call and omits `refundETH()` from their multicall, the excess ETH is stranded in the router. Any subsequent caller can either steal it directly via `refundETH()`, or have it silently consumed on their behalf by `pay()`, causing direct loss of the original depositor's principal.

---

### Finding Description

**Path 1 — `refundETH()` theft**

`refundETH()` is defined as:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

It transfers `address(this).balance` — the entire router balance — to `msg.sender`. There is no accounting of who deposited which ETH. If any ETH from a prior transaction is stranded in the router, the next caller of `refundETH()` receives it all.

**Path 2 — `pay()` silent consumption**

`pay()` is defined as:

```solidity
// PeripheryPayments.sol L69-88
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
        uint256 nativeBalance = address(this).balance;   // ← entire contract balance
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
    } else { ... }
}
```

When `token == WETH`, the function reads `address(this).balance` and uses it to wrap ETH before pulling from the payer. If stranded ETH from User A is present, User B's swap callback will consume it — User B's `safeTransferFrom` pull is reduced or eliminated entirely, and User A's ETH is gone.

**How ETH becomes stranded**

Every payable swap entry point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`) accepts `msg.value`. For exact-output swaps, the user must send up to `amountInMaximum` ETH upfront; the actual amount consumed is determined only during the swap callback. The surplus is left in the router. Recovery requires an explicit `refundETH()` call in the same multicall. If the user calls the swap function directly (not via multicall), or forgets to append `refundETH()`, the surplus is permanently stranded until another party claims it.

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks unsolicited direct ETH pushes; it does not prevent ETH from accumulating via `msg.value` on payable functions.

---

### Impact Explanation

**Direct loss of user principal.** A user who calls `exactOutputSingle{value: 2 ETH}` where the actual amountIn is 1 ETH, without appending `refundETH()`, loses 1 ETH to the next caller of `refundETH()` or to the next WETH swapper whose `pay()` call consumes it. The loss is permanent and proportional to the overpayment. No privileged role is required to trigger the loss; any unprivileged address can call `refundETH()` at any time.

---

### Likelihood Explanation

**Medium.** The Uniswap v3 multicall pattern requires users to explicitly append `refundETH()` to recover unused ETH. This is a well-known footgun: users who call `exactOutputSingle` or `exactOutput` directly (without multicall), or who construct a multicall without the refund step, will strand ETH. Frontrunners monitoring the mempool can observe a stranded-ETH state after any such transaction and immediately call `refundETH()` to drain it. The `pay()` consumption path requires no active attacker — it fires automatically on the next WETH swap.

---

### Recommendation

1. **Track per-transaction ETH**: Store `msg.value` at entry and use only that tracked amount in `pay()`, rather than `address(this).balance`. Clear the tracked value at the end of each top-level call.

2. **Restrict `refundETH()` to the depositor**: Record the depositor address (e.g., in transient storage) at the start of each payable entry point and enforce it in `refundETH()`.

3. **Auto-refund surplus**: After each swap, compute `address(this).balance - expectedRemainder` and push it back to `msg.sender` automatically, eliminating the need for a separate `refundETH()` call.

---

### Proof of Concept

```
1. Alice calls exactOutputSingle{value: 2 ether}(
       tokenIn=WETH, amountOut=X, amountInMaximum=2 ether, ...
   )
   - Pool callback fires; pay() sees nativeBalance=2 ETH, value=1 ETH
   - pay() wraps 1 ETH → sends WETH to pool
   - 1 ETH remains in router
   - Alice does NOT call refundETH() (called directly, not via multicall)

2. Bob calls refundETH() in a standalone tx
   - balance = address(this).balance = 1 ETH
   - _transferETH(Bob, 1 ETH) executes
   - Bob receives Alice's 1 ETH

   OR

2'. Bob calls exactInputSingle{value: 0}(tokenIn=WETH, amountIn=0.5 ETH, ...)
   - pay() sees nativeBalance=1 ETH >= 0.5 ETH
   - pay() wraps 0.5 ETH from Alice's stuck balance → Bob's swap is fully funded
   - Bob pays 0 ETH from his own balance; Alice loses 0.5 ETH
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
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
