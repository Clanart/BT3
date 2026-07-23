Audit Report

## Title
Unrestricted `refundETH()` and Total-Balance `pay()` Allow Theft of Stranded Router ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`exactInput` (and `exactInputSingle`) are `payable` but never refund excess `msg.value`. The pool callback consumes only the exact `amountIn` via `pay()`, which draws from `address(this).balance` — the router's total ETH, not the caller's `msg.value`. Any surplus ETH is silently stranded on the router. Because `refundETH()` has no access control, any attacker can immediately drain that surplus, or call `exactInput` with `msg.value = 0` and have their WETH swap funded by the victim's residue.

## Finding Description

**Root cause 1 — `exactInput` strands excess ETH.**

`exactInput` is `external payable` but contains no post-swap ETH refund: [1](#0-0) 

The callback `_justPayCallback` calls `pay()` with the exact amount the pool requested — not `msg.value`: [2](#0-1) 

**Root cause 2 — `pay()` uses the router's total ETH balance.**

When `token == WETH` and `payer != address(this)`, `pay()` reads `address(this).balance` — the entire router balance, including ETH stranded by prior callers: [3](#0-2) 

**Root cause 3 — `refundETH()` has no access control.**

Any address can call `refundETH()` and receive the router's full ETH balance: [4](#0-3) 

There is no check that `msg.sender` is the original depositor, no per-user accounting, and no reentrancy guard.

**Root cause 4 — `sweepToken` and `unwrapWETH9` are equally unrestricted.**

Both are `public payable` with no caller restriction and accept an arbitrary `recipient`: [5](#0-4) 

**Note on `receive()`:** The `receive()` function restricts plain ETH transfers to WETH only, but this does not prevent ETH from being sent via `payable` function calls such as `exactInput`. The stranding vector is fully reachable. [6](#0-5) 

## Impact Explanation

Direct, unconditional loss of user ETH principal. A victim who sends `msg.value > amountIn` (a common defensive pattern to avoid reverts from minor price movement) loses the surplus to the first attacker who calls `refundETH()`. Alternatively, the attacker can call `exactInput` with `msg.value = 0` and a WETH input path; `pay()` will wrap the victim's stranded ETH to fund the attacker's swap output. Both attack vectors require no special role, no token approval, and no capital. This meets the Sherlock Critical/High threshold for direct loss of user principal.

## Likelihood Explanation

- `exactInput` is a primary public entrypoint; WETH-input paths are a standard use case.
- Users routinely send a small ETH buffer above `amountIn` to avoid reverts from price movement.
- `refundETH()` is trivially callable with no mempool monitoring required — an attacker can simply poll the router's ETH balance and call `refundETH()` whenever it is non-zero.
- The attack is atomic, requires no capital, and is repeatable indefinitely.

## Recommendation

1. **Auto-refund at the end of `exactInput` and `exactInputSingle`.** After the slippage check, call `_transferETH(msg.sender, address(this).balance)` before returning, or track `msg.value` in a transient slot and refund the remainder.
2. **Restrict `pay()` to the current call's ETH.** Record `msg.value` in a transient slot at entry and use it as the ceiling instead of `address(this).balance`.
3. **Restrict `refundETH()` to the original depositor**, or add a guard that reverts if called outside a `multicall` context. If multicall-only usage is the design intent, document and enforce it.
4. **Restrict `sweepToken` and `unwrapWETH9`** similarly, or require `amountMinimum > 0` so callers cannot drain dust left by others.

## Proof of Concept

```solidity
// Setup: router has 0 ETH initially.

// Step 1 — Victim calls exactInput with a safety buffer:
//   params.amountIn  = 0.5 ETH (WETH path)
//   msg.value        = 1.0 ETH
router.exactInput{value: 1 ether}(params);
// Pool callback fires; pay() wraps exactly 0.5 ETH → sends WETH to pool.
// 0.5 ETH remains on router. exactInput returns without refunding.

// Step 2 — Attacker (any address) calls refundETH():
router.refundETH();
// _transferETH(attacker, 0.5 ETH) executes.
// Attacker receives victim's 0.5 ETH. No approval, no role, no capital needed.

// --- Alternative: free swap ---
// Attacker calls exactInput with WETH input, msg.value = 0:
router.exactInput{value: 0}(attackerParams); // amountIn = 0.3 ETH, token[0] = WETH
// pay() sees nativeBalance = 0.5 ETH >= 0.3 ETH
// Wraps 0.3 ETH of victim's residue, sends WETH to pool.
// Attacker receives swap output. Victim loses 0.3 ETH.
```

The root cause is `address(this).balance` in `pay()` at `PeripheryPayments.sol:74`, combined with the absence of any automatic refund in `exactInput` (`MetricOmmSimpleRouter.sol:92–125`) and the unrestricted `refundETH()` at `PeripheryPayments.sol:58–63`.

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-55)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }

  /// @inheritdoc IPeripheryPayments
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
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
