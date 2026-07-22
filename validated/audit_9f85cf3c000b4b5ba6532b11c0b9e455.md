The vulnerability claim is real. Let me trace through the exact code path.

**`pay()` in `PeripheryPayments.sol` (lines 73–84):**

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ALL ETH in router, no ownership tracking
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {                  // ← contested branch
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

`address(this).balance` is the **total** router ETH balance with no per-caller accounting. Any ETH left in the router from a prior call is silently consumed.

**How ETH gets stranded:** `receive()` blocks direct sends (only WETH can send ETH), but every swap entry point is `payable` — `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`. A user who sends excess ETH and omits `refundETH` leaves ETH in the router permanently until someone else's `pay()` call drains it.

**Cross-transaction attack path:**

1. User A calls `exactInputSingle` (WETH in, amountIn = 0.5 ETH), sends 1 ETH, no `refundETH`. `pay()` wraps 0.5 ETH; 0.5 ETH remains in the router.
2. Attacker calls `exactInputSingle` (WETH in, amountIn = 1 ETH), sends 0 ETH. `nativeBalance = 0.5 ETH`, `value = 1 ETH` → hits the `nativeBalance > 0 && nativeBalance < value` branch. Router wraps user A's 0.5 ETH, sends WETH to pool, then pulls only 0.5 ETH worth of WETH from attacker via `transferFrom`. Attacker pays half price; user A loses 0.5 ETH with no swap output.

The `nativeBalance >= value` branch is even worse: if enough ETH is stranded, the attacker pays **zero** WETH.

**The "same multicall" framing in the question is incorrect.** `multicall` has a single `msg.sender`, so steps within one multicall share the same payer — there is no intra-multicall cross-user theft. The real attack is **cross-transaction**: stranded ETH from transaction N is consumed by transaction N+1's `pay()`.

---

### Title
Stranded Router ETH Consumed by Subsequent Callers' WETH Payments — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay()` uses `address(this).balance` with no per-caller ownership tracking. ETH left in the router by any prior payable call is silently consumed to subsidise a later caller's WETH payment, causing the original depositor to lose ETH with no swap output.

### Finding Description
`pay()` reads `nativeBalance = address(this).balance` and, when `token == WETH`, uses that balance to wrap ETH before falling back to `transferFrom`. [1](#0-0) 

Every public swap entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) is `payable`. [2](#0-1) [3](#0-2) 

`receive()` blocks direct ETH sends but cannot block ETH attached to payable function calls. [4](#0-3) 

If a caller sends excess ETH and omits `refundETH`, the surplus persists in the router. A subsequent caller whose `pay()` hits either the `nativeBalance >= value` branch (full free swap) or the `nativeBalance > 0 && nativeBalance < value` branch (partial subsidy) consumes that ETH without any entitlement to it. [5](#0-4) 

### Impact Explanation
Direct loss of user principal: the original ETH sender loses ETH without receiving swap output. The exploiting caller receives a free or discounted WETH payment funded by the victim's ETH. This satisfies the "Critical/High/Medium direct loss of user principal" gate.

### Likelihood Explanation
Medium. ETH stranding requires a user to send excess ETH without `refundETH` — a common pattern in DeFi multicall usage. Once stranded, any attacker monitoring `address(router).balance` can immediately exploit it in the next block with a zero-ETH `exactInputSingle` call.

### Recommendation
Track per-call ETH entitlement in transient storage: record `msg.value` at the start of each payable entry point and allow `pay()` to consume only up to that recorded amount. Alternatively, enforce that `pay()` only wraps ETH equal to `msg.value` of the current top-level call, and revert if `address(this).balance` exceeds that amount at entry (indicating stranded ETH from a prior call).

### Proof of Concept
```solidity
// 1. User A sends excess ETH, no refundETH
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    tokenIn: WETH, amountIn: 0.5 ether, ...
}));
// Router now holds 0.5 ETH (stranded)

// 2. Attacker sends 0 ETH, amountIn = 1 ETH
// pay() sees nativeBalance=0.5, value=1 → hits partial branch:
//   wraps 0.5 ETH (user A's), transferFrom attacker only 0.5 WETH
// Attacker pays 50% of required input; user A loses 0.5 ETH.
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    tokenIn: WETH, amountIn: 1 ether, ...
}));
assertEq(address(router).balance, 0);          // stranded ETH consumed
assertEq(userA_ethLost, 0.5 ether);            // victim lost principal
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-84)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```
