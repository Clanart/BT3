The vulnerability is real. Let me trace the exact execution path through the code.

### Title
Stranded ETH from Prior User's Unrefunded Payable Call Is Consumed by Subsequent User's WETH Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` — the router's entire native ETH balance — as the funding source for any WETH payment, without tracking which ETH belongs to which caller. If a prior user sent more ETH than their swap consumed and did not call `refundETH()`, that stranded ETH is silently consumed to fund a subsequent user's WETH swap.

---

### Finding Description

`exactInputSingle` (and all other `payable` router entry points) accept arbitrary `msg.value`. After the swap, any excess ETH remains in the router until the user explicitly calls `refundETH()`. There is no automatic refund and no per-user accounting.

When the pool callback fires, `_justPayCallback` calls:

```solidity
pay(_getTokenToPay(), _getPayer(), msg.sender, value);
```

Inside `pay()`, the WETH branch reads the **whole contract balance**:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ALL ETH in router
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

`nativeBalance` is not scoped to the current caller's deposit. Any ETH left by a previous user satisfies the `nativeBalance >= value` branch, wraps that ETH, and transfers it to the pool — without touching the current payer's WETH allowance.

The entry point that leaves ETH stranded is `exactInputSingle`, which is `payable` and performs no automatic refund: [2](#0-1) 

The callback context correctly records `payer = msg.sender` and `tokenToPay = params.tokenIn`, but `pay()` ignores `payer` entirely when the native balance is sufficient: [3](#0-2) 

---

### Impact Explanation

**Direct loss of user principal.** User A's ETH is irreversibly consumed to settle User B's swap. User A receives nothing in return; their ETH is gone. User B's WETH allowance is never touched, so they receive a fully subsidized swap. This is a concrete, measurable theft of funds above any Sherlock High threshold.

---

### Likelihood Explanation

- `exactInputSingle` is the primary router entry point; any user who sends `msg.value > amountIn` (e.g., to avoid a revert on partial fills) and omits `refundETH()` creates the precondition.
- An attacker can monitor the mempool or simply call `exactInputSingle{value:0}` with `tokenIn=WETH` speculatively — if the router holds no ETH the call falls through to `safeTransferFrom` and costs only gas.
- No privileged access, no malicious pool, no non-standard token required.

---

### Recommendation

Track each caller's deposited ETH separately (e.g., a transient per-caller balance slot), or restrict the ETH-wrapping path to only the ETH that arrived with the current transaction (`msg.value`). Alternatively, automatically refund excess ETH at the end of every `payable` entry point before returning, so no ETH can accumulate between calls.

---

### Proof of Concept

```
1. User A: exactInputSingle{value: 1 ether}(tokenIn=WETH, amountIn=0.5 ether, ...)
   → pay() wraps 0.5 ETH, sends to pool. Router holds 0.5 ETH.
   → User A does NOT call refundETH().

2. User B (attacker): exactInputSingle{value: 0}(tokenIn=WETH, amountIn=0.5 ether, ...)
   → pay() reads address(this).balance = 0.5 ether
   → nativeBalance (0.5) >= value (0.5) → wraps User A's 0.5 ETH, sends to pool
   → User B's WETH allowance: untouched
   → User A's 0.5 ETH: consumed

Assert: User A's 0.5 ETH is gone; User B paid 0 WETH for a 0.5 WETH swap.
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-84)
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
