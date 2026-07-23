The code is clear enough to render a verdict. Let me trace the exact execution path.

The execution path is fully traceable in the scoped code. The vulnerability is real.

---

### Title
Stranded ETH from Prior User Consumed by Attacker's WETH Swap via `PeripheryPayments.pay()` Partial-ETH Branch — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses the router's entire native ETH balance (`address(this).balance`) as a subsidy when paying for a WETH swap, with no per-user accounting. Any ETH left on the router by a prior user who did not call `refundETH()` is silently consumed to cover part of a subsequent caller's swap input, causing direct loss of the prior user's ETH principal.

---

### Finding Description

The `pay()` function contains three branches for `token == WETH`: [1](#0-0) 

The partial-ETH branch (lines 78–81) fires whenever `0 < address(this).balance < value`. It deposits **all** of the router's current ETH balance as WETH and transfers it to the pool, then pulls only the remainder from the current `payer` via `safeTransferFrom`. There is no mechanism that tracks which user deposited which ETH; the function treats the entire router balance as freely available.

ETH can be stranded on the router because `multicall`, `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` are all `payable`: [2](#0-1) 

The `receive()` guard only blocks direct ETH transfers from non-WETH addresses; it does **not** block ETH arriving via a `payable` function call. A user who sends excess ETH (e.g., to cover a WETH swap) and omits the separate `refundETH()` call leaves that ETH permanently accessible to the next caller. [3](#0-2) 

---

### Impact Explanation

**Direct loss of prior user's ETH principal.**

- User A calls `exactInputSingle{value: 2 ETH}` with `tokenIn=WETH`, `amountIn=1 ETH`. The swap consumes 1 ETH; 1 ETH remains on the router because A did not call `refundETH()`.
- Attacker B calls `exactInputSingle` (no ETH sent) with `tokenIn=WETH`, `amountIn=2 ETH`.
- The pool callback invokes `pay(WETH, B, pool, 2e18)`.
- `nativeBalance = 1 ETH` → partial branch: router deposits A's 1 ETH as WETH, sends it to the pool, then pulls only 1 WETH from B.
- A's 1 ETH is irrecoverably consumed. B's effective cost is halved.

The lost amount is bounded by `min(stranded ETH, amountIn)` and can be arbitrarily large depending on how much ETH A left behind.

---

### Likelihood Explanation

- `refundETH()` is a separate, optional call; users who wrap ETH swaps in a single `multicall` without appending `refundETH()` will routinely leave dust or full excess ETH on the router.
- The router's ETH balance is publicly readable on-chain; an attacker can monitor it and trigger the exploit in the very next block.
- No special role, malicious pool, or non-standard token is required — only a legitimate WETH swap through a factory-registered pool.

---

### Recommendation

Remove the partial-ETH hybrid branch entirely. The router should either:

1. **Use only native ETH** (when `msg.value >= value`, deposit exactly `value` and refund the rest atomically), or
2. **Use only `safeTransferFrom`** (pull WETH from the payer directly).

Mixing the two without per-user ETH accounting is the root cause. Additionally, consider tracking per-call ETH budgets in transient storage so that `pay()` can only spend ETH that arrived in the current top-level call.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Foundry integration test (pseudo-code outline)
function test_strandedEthConsumedByAttacker() public {
    // 1. User A swaps WETH->tokenOut, sends 2 ETH, amountIn = 1 ETH
    //    A forgets refundETH(); 1 ETH remains on router.
    vm.prank(userA);
    router.exactInputSingle{value: 2 ether}(ExactInputSingleParams({
        tokenIn: WETH, amountIn: 1 ether, ...
    }));
    assertEq(address(router).balance, 1 ether); // A's leftover

    // 2. Attacker B approves only 1 WETH (not 2), calls exactInputSingle amountIn=2 ETH
    deal(WETH, attacker, 1 ether);
    IERC20(WETH).approve(address(router), 1 ether);
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        tokenIn: WETH, amountIn: 2 ether, ...
    }));

    // 3. Assert: router ETH balance is 0 (A's ETH consumed),
    //    B's WETH balance is 0 (only 1 WETH pulled from B),
    //    pool received full 2 WETH worth of input.
    assertEq(address(router).balance, 0);
    assertEq(IERC20(WETH).balanceOf(attacker), 0);
    // A's 1 ETH is gone; B paid only 1 WETH instead of 2.
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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
