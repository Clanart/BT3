Looking at the actual code to verify the claim:

Audit Report

## Title
Router `pay()` WETH branch consumes unattributed contract ETH balance, enabling theft of stranded native ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

The `pay()` internal helper in `PeripheryPayments.sol` settles WETH obligations by reading `address(this).balance` — a shared, unattributed pool — rather than the ETH contributed by the current caller. Because `exactInputSingle` (and other payable entry-points) do not enforce full consumption of `msg.value` and do not automatically refund leftover ETH, any subsequent WETH swapper can have their payment obligation silently satisfied with a prior user's stranded ETH, causing permanent loss of that ETH.

## Finding Description

**Root cause — `pay()` uses global contract balance:** [1](#0-0) 

`nativeBalance = address(this).balance` is the router's total ETH, not the current caller's contribution. Any ETH left on the router from a prior transaction is consumed first.

**ETH stranding path — `exactInputSingle` with a price-limit partial fill:** [2](#0-1) 

The function is `payable` and accepts arbitrary `msg.value`. The pool callback (`_justPayCallback`) pays only the amount the pool actually consumed — `extractPositiveAmount(amount0Delta, amount1Delta)` — not `params.amountIn`. If a tight `priceLimitX64` causes a partial fill, the callback requests less than `msg.value`, and the remainder stays on the router. There is no automatic `refundETH()` after the swap settles. [3](#0-2) 

**Why the `receive()` guard does not prevent stranding:** [4](#0-3) 

`receive()` only fires on plain ETH transfers (no calldata). `msg.value` sent alongside a payable function call bypasses `receive()` entirely and is silently accepted by the EVM, so ETH can accumulate on the router across transactions.

**Exploit flow:**
1. Alice calls `exactInputSingle{value: 1 ether}` with `tokenIn = WETH`, `amountIn = 1 ether`, and a tight `priceLimitX64`. The pool partially fills, consuming 0.5 ether. The callback calls `pay(WETH, Alice, pool, 0.5 ether)`; `address(this).balance = 1 ether ≥ 0.5 ether`, so the router wraps 0.5 ether and forwards it. 0.5 ether remains on the router. Alice does not call `refundETH()`.
2. Bob calls `exactInputSingle{value: 0}` with `tokenIn = WETH`, `amountIn = 0.5 ether`. The callback calls `pay(WETH, Bob, pool, 0.5 ether)`; `address(this).balance = 0.5 ether ≥ 0.5 ether`, so the router wraps Alice's ETH and forwards it to the pool. Bob's swap settles in full; Bob spent 0 ETH and 0 WETH. Alice's 0.5 ether is permanently lost.

**Existing guards are insufficient:** `_requireExpectedCallbackCaller` only validates the pool identity, not the ETH source. `refundETH()` is a manual, optional step that sends the entire balance to whoever calls it first — itself a secondary theft vector. [5](#0-4) 

## Impact Explanation

Direct loss of user-deposited native ETH. Any user who sends `msg.value` with a WETH swap and receives a partial fill (or over-sends) permanently loses the residual ETH to the next WETH swapper. This is a High-severity direct loss of user principal, matching the "Critical/High/Medium direct loss of user principal" allowed impact gate.

## Likelihood Explanation

Medium. Partial fills are a normal outcome when `priceLimitX64` is set (a common frontend pattern). `multicall` users frequently omit `refundETH()` as a trailing step. An MEV bot can monitor `address(router).balance` on-chain and front-run the victim's `refundETH()` call with a zero-cost WETH swap. No privileged role, malicious token, or non-standard ERC-20 is required.

## Recommendation

Attribute ETH to the current transaction rather than the global contract balance. Two concrete options:

1. **Snapshot `msg.value` at entry**: capture `uint256 ethBudget = msg.value` in transient storage alongside the callback context and replace `address(this).balance` in `pay()` with that budget, consuming only what the current caller deposited.
2. **Unconditional post-swap refund**: after every swap entry-point clears the callback context, call `_transferETH(msg.sender, address(this).balance)` (guarded by `balance > 0`) so no ETH persists across transactions.

## Proof of Concept

```solidity
// Step 1 – Alice strands ETH
// Alice calls exactInputSingle{value: 1 ether}:
//   tokenIn = WETH, amountIn = 1 ether, priceLimitX64 = <tight limit>
// Pool partially fills: callback fires pay(WETH, Alice, pool, 0.5 ether).
// address(this).balance = 1 ether >= 0.5 ether → wraps 0.5 ether, sends to pool.
// 0.5 ether remains on router. Alice never calls refundETH().

// Step 2 – Bob steals Alice's ETH
// Bob calls exactInputSingle{value: 0}:
//   tokenIn = WETH, amountIn = 0.5 ether
// Callback fires pay(WETH, Bob, pool, 0.5 ether).
// address(this).balance = 0.5 ether >= 0.5 ether → wraps Alice's ETH, sends to pool.
// Bob's swap settles; Bob spent 0 ETH and 0 WETH. Alice's 0.5 ether is gone.

// Foundry test sketch:
// 1. Deploy router with real WETH and a pool that supports price limits.
// 2. vm.deal(alice, 1 ether); alice calls exactInputSingle{value: 1 ether} with tight limit.
// 3. Assert address(router).balance == 0.5 ether after Alice's swap.
// 4. bob calls exactInputSingle{value: 0} for 0.5 ether WETH.
// 5. Assert bob received output tokens; assert alice's 0.5 ether is gone from router.
// 6. Assert bob's WETH balance and ETH balance are both unchanged (paid nothing).
```

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
