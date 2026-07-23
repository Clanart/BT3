### Title
Pre-existing ETH balance consumed by subsequent WETH swap callers, draining stranded user ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` — the router's **entire** native ETH balance — when paying for a WETH swap, without subtracting any initial balance that existed before the current transaction. This is the direct analog of the SwapperV2 bug: just as SwapperV2 used `newBalance` (end balance) instead of `newBalance - initialBalance` when the action did not increase the balance, `pay()` uses the full `nativeBalance` without isolating the ETH that was actually sent for the current swap.

---

### Finding Description

In `PeripheryPayments.pay()`, when `token == WETH` and `payer != address(this)`, the function reads the router's live ETH balance and uses it to wrap-and-forward WETH to the pool:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);          // payer charged nothing
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // payer undercharged
} else {
    IERC20(WETH).safeTransferFrom(payer, recipient, value);
}
``` [1](#0-0) 

`nativeBalance` is never bounded to "ETH sent for this specific swap." If any ETH was left in the router by a prior user (e.g., they sent excess `msg.value` and did not call `refundETH()`), that ETH is silently consumed to pay the current caller's swap obligation.

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does **not** prevent ETH from accumulating via `msg.value` on any of the `payable` entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`, `addLiquidityExactShares`, etc.). [2](#0-1) 

The SwapperV2 fix was to change the else-clause from `newBalance` to `0`. The analogous fix here is to snapshot the ETH balance **before** the swap begins and pass only the delta (`msg.value` for this call) into `pay()`, so pre-existing ETH is never consumed.

---

### Impact Explanation

A user who sends excess ETH with a WETH swap and omits `refundETH()` leaves ETH stranded in the router. Any subsequent caller whose swap token is WETH will have their payment fully or partially covered by that stranded ETH:

- **Branch 1** (`nativeBalance >= value`): the subsequent caller pays **zero** WETH from their wallet; the entire swap is funded by the prior user's ETH.
- **Branch 2** (`0 < nativeBalance < value`): the subsequent caller pays only `value - nativeBalance` WETH; the shortfall is covered by the prior user's ETH.

In both cases the prior user's ETH is permanently lost — it is wrapped and forwarded to the pool on behalf of a different address. The pool receives the correct amount and is unaffected; the loss is borne entirely by the user whose ETH was stranded.

---

### Likelihood Explanation

- The router exposes multiple `payable` entry points. Users routinely send ETH with WETH swaps and rely on a separate `refundETH()` call (often bundled in `multicall`) to recover excess. Any failure to include that call — due to user error, a reverted multicall leg, or an integrator omission — leaves ETH in the router.
- The stranded balance is publicly visible on-chain. A griever or MEV bot can monitor the router's ETH balance and immediately issue a zero-`msg.value` WETH swap to drain it.
- No privileged access is required; any address can call `exactInputSingle` with `tokenIn = WETH` and `msg.value = 0`.

---

### Recommendation

Snapshot the router's ETH balance at the start of each swap entry point and pass only the ETH attributable to the current call into `pay()`. One approach:

```solidity
// In exactInputSingle / exactInput / exactOutput* before calling pool.swap():
uint256 ethForThisSwap = msg.value; // ETH the caller explicitly sent

// In pay(), replace:
uint256 nativeBalance = address(this).balance;
// with:
uint256 nativeBalance = ethForThisSwap; // passed as a parameter or stored in transient storage
```

Alternatively, store `msg.value` in a transient slot alongside the other callback context fields in `TransientCallbackPool`, and read it inside `pay()` instead of `address(this).balance`. [3](#0-2) 

---

### Proof of Concept

1. **Setup**: Router is deployed; no ETH in router initially.

2. **Step 1 — Victim**: Alice calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = 1 WETH`, `msg.value = 10 ETH`. The swap executes; the callback calls `pay(WETH, Alice, pool, 1e18)`. `nativeBalance = 10 ETH >= 1 ETH`, so 1 ETH is wrapped and forwarded. Alice receives her output token. She does **not** call `refundETH()`. **9 ETH remains in the router.**

3. **Step 2 — Attacker**: Bob monitors the router and sees 9 ETH. Bob calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = 5 WETH`, `msg.value = 0`. The pool executes the swap and calls `metricOmmSwapCallback`, which calls `pay(WETH, Bob, pool, 5e18)`. `nativeBalance = 9 ETH >= 5 ETH`, so 5 ETH is wrapped and forwarded to the pool. Bob's WETH allowance is never touched. Bob receives 5 WETH worth of output for free.

4. **Result**: Alice loses 5 ETH (on top of the 1 ETH used for her own swap). Bob receives a fully subsidized swap. The pool is solvent and unaware of the discrepancy. [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/libraries/TransientCallbackPool.sol (L10-13)
```text
  uint256 private constant T_SLOT = 0;
  uint256 private constant T_AMOUNT_IN_SLOT = 1;
  uint256 private constant T_PAYER_SLOT = 2;
  uint256 private constant T_TOKEN_TO_PAY_SLOT = 3;
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
