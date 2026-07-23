Audit Report

## Title
Cross-User ETH Isolation Failure in `PeripheryPayments.pay` — Stranded ETH Subsidises Another User's WETH Input — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay` reads `address(this).balance` — the router's entire native ETH balance — when settling a WETH-input swap. Because `multicall` is `payable` and `refundETH()` is a separate, optional call, ETH sent by User A in a prior transaction can remain stranded in the router and be silently consumed to cover User B's WETH obligation in a later, independent transaction. User A suffers a permanent, direct loss of ETH with no corresponding output.

## Finding Description

The vulnerable branch in `pay` reads the full contract balance unconditionally:

```solidity
// PeripheryPayments.sol lines 73-84
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // entire router balance
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

There is no per-user or per-transaction ETH accounting. `address(this).balance` aggregates ETH from all sources: the current `msg.value`, any prior stranded ETH, and WETH-unwrap proceeds.

ETH becomes stranded because `multicall` is `payable`: [2](#0-1) 

and `refundETH()` is a separate, optional call that users must explicitly include: [3](#0-2) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) does **not** prevent stranding — it only applies to bare ETH transfers, not to ETH sent alongside a function call such as `multicall{value: X}(...)` or `exactInputSingle{value: X}(...)`. [4](#0-3) 

The transient callback context stored in `MetricOmmSwapRouterBase` tracks pool, callback mode, payer, token, and `amountIn` — but never `msg.value`, so there is no existing mechanism to cap ETH consumption to the current call's contribution. [5](#0-4) 

**Exploit flow:**
1. User A calls `multicall{value: 1 ether}([exactInputSingle({tokenIn: tokenA, tokenOut: tokenB, ...})])` — no `refundETH()` included. The swap settles in ERC-20; 1 ETH remains in the router.
2. User B calls `exactInputSingle({tokenIn: WETH, tokenOut: tokenX, amountIn: 2 ether, ...})` with only 1 WETH approved to the router.
3. The pool callback fires → `pay(WETH, userB, pool, 2 ether)`:
   - `nativeBalance = address(this).balance = 1 ether` (User A's stranded ETH).
   - Router wraps and transfers 1 ETH as WETH to the pool.
   - Pulls only 1 WETH from User B via `transferFrom`.
4. User A's 1 ETH is permanently gone; User B paid only 1 WETH instead of 2.

## Impact Explanation

Direct, permanent loss of User A's ETH principal. User A's ETH is wrapped into WETH and transferred to a pool to settle User B's swap obligation; User A receives nothing in return. This is a fund-loss impact meeting Critical/High Sherlock thresholds: it violates the cross-user ETH isolation invariant and constitutes swap conservation failure (the pool receives its owed input, but from the wrong source).

## Likelihood Explanation

No privileged access is required. Users routinely call `multicall{value: X}` to pay with ETH for ETH-input swaps. If the multicall omits `refundETH()` — a common omission when the swap consumed less than `X`, or when the token was not WETH — the surplus is stranded. Any subsequent WETH-input swap by any user will silently drain the stranded balance. A griever can deliberately trigger this: observe a stranded-ETH transaction in the mempool, then immediately follow with a WETH swap sized to consume exactly the stranded amount.

## Recommendation

Track only the ETH belonging to the current call by recording `msg.value` at swap entry (e.g., in transient storage alongside the existing callback context) and using that as the cap in `pay`, rather than `address(this).balance`:

```solidity
// In pay(), replace:
uint256 nativeBalance = address(this).balance;
// With:
uint256 nativeBalance = _currentMsgValue(); // stored in transient storage at swap entry
```

Alternatively, auto-refund any remaining ETH at the end of each top-level swap entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`), or enforce that `refundETH()` is always the last call in every `multicall` that sends ETH.

## Proof of Concept

1. Deploy `MetricOmmSimpleRouter` with real WETH, a factory-registered `tokenA/tokenB` pool (non-WETH), and a `WETH/tokenX` pool.
2. **User A** calls `multicall{value: 1 ether}([exactInputSingle({tokenIn: tokenA, tokenOut: tokenB, amountIn: ..., ...})])` — no `refundETH()`. Swap settles in ERC-20; 1 ETH stranded.
3. Assert `address(router).balance == 1 ether`.
4. **User B** approves only `1 WETH` to the router, then calls `exactInputSingle({tokenIn: WETH, tokenOut: tokenX, amountIn: 2 ether, ...})`.
5. Pool callback fires → `pay(WETH, userB, pool, 2 ether)`: wraps 1 ETH from router, pulls 1 WETH from User B.
6. Assert:
   - `address(router).balance == 0` (User A's ETH consumed).
   - User B's WETH balance decreased by 1, not 2.
   - User A's ETH balance permanently reduced by 1 ETH with no output received.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L29-32)
```text
  function _setNextCallbackContext(address pool, uint8 callbackMode, address payer, address tokenToPay) internal {
    _requireFactoryPool(pool);
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
  }
```
