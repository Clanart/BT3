The `pay` function in `PeripheryPayments.sol` is the critical piece. Let me trace the exact path:

### Title
Cross-User ETH Isolation Failure in `PeripheryPayments.pay` — Stranded ETH from One User Subsidises Another's WETH Input — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay` uses `address(this).balance` — the router's **entire** native ETH balance — when settling a WETH-input swap. Because `multicall` is `payable` and `refundETH()` is a separate, optional call, ETH sent by User A in a prior transaction can remain in the router and be silently consumed to cover User B's WETH obligation in a later, independent transaction.

---

### Finding Description

The vulnerable branch in `pay` is:

```solidity
// PeripheryPayments.sol lines 73-84
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← entire router balance
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // ← only pulls remainder from payer
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

There is no per-user or per-transaction ETH accounting. `address(this).balance` aggregates ETH from all sources: the current `msg.value`, any prior stranded ETH, and WETH-unwrap proceeds.

ETH becomes stranded because `multicall` is `payable`:

```solidity
// MetricOmmSimpleRouter.sol line 39
function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
``` [2](#0-1) 

and `refundETH()` is a separate, optional call that users must explicitly include:

```solidity
// PeripheryPayments.sol lines 58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [3](#0-2) 

---

### Impact Explanation

**Direct, permanent loss of User A's ETH.** User A's ETH is wrapped into WETH and transferred to a pool to settle User B's swap obligation. User A receives nothing in return. User B's required WETH approval is reduced by the amount of stranded ETH consumed. This violates the CROSS-USER-ETH-ISOLATION invariant: each user's ETH must only be spent for their own swap.

---

### Likelihood Explanation

The scenario is realistic and requires no privileged access:

- Users routinely call `multicall{value: X}` to pay with ETH for WETH swaps. If the multicall omits `refundETH()` (e.g., the swap consumed less than `X`, or the token was not WETH), the surplus is stranded.
- Any subsequent WETH-input swap by any user will silently drain the stranded balance.
- A griever can deliberately trigger this: observe a stranded-ETH transaction in the mempool, then front-run or immediately follow with a WETH swap sized to consume exactly the stranded amount.

---

### Recommendation

Track only the ETH that belongs to the **current call** by recording `msg.value` at entry and using that as the cap, rather than `address(this).balance`. One approach:

```solidity
// In pay(), replace:
uint256 nativeBalance = address(this).balance;
// With:
uint256 nativeBalance = _currentMsgValue(); // stored in transient storage at swap entry
```

Alternatively, enforce that the router holds zero ETH between transactions by requiring `refundETH()` to be the last call in every `multicall` that sends ETH, or by auto-refunding at the end of each top-level swap entry point.

---

### Proof of Concept

1. Deploy `MetricOmmSimpleRouter` with a real WETH and a factory-registered pool for `tokenA/tokenB` (non-WETH pair) and a pool for `WETH/tokenX`.
2. **User A** calls `multicall{value: 1 ether}([exactInputSingle({tokenIn: tokenA, tokenOut: tokenB, amountIn: ...})])` — no `refundETH()` included. The swap settles in ERC-20; 1 ETH remains in the router.
3. Assert `address(router).balance == 1 ether`.
4. **User B** approves only `1 WETH` to the router, then calls `exactInputSingle({tokenIn: WETH, tokenOut: tokenX, amountIn: 2 ether, ...})`.
5. The pool callback fires → `pay(WETH, userB, pool, 2 ether)`:
   - `nativeBalance = 1 ether` → wraps and transfers 1 ETH as WETH to pool.
   - Pulls only `1 WETH` from User B via `transferFrom`.
6. Assert:
   - `address(router).balance == 0` (User A's ETH is gone).
   - User B's WETH balance decreased by only 1 (not 2).
   - User A's ETH balance is permanently reduced by 1 ETH with no corresponding output.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```
