The `pay` function in `PeripheryPayments.sol` is the critical code to analyze. Let me verify the exact behavior:

The vulnerability is confirmed. Here is the full analysis:

---

### Title
Stranded ETH in Router Subsidizes Subsequent User's WETH Input Payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`PeripheryPayments.pay` uses the router's entire `address(this).balance` as a WETH substitute for any caller whose `tokenIn` is WETH, with no per-user accounting. ETH stranded in the router from a prior user's `multicall{value: X}` (where `refundETH` was omitted) is silently consumed to cover a subsequent user's WETH input, permanently destroying the prior user's ETH.

### Finding Description

`pay` in `PeripheryPayments.sol` handles the `token == WETH, payer != address(this)` case as follows: [1](#0-0) 

```solidity
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
```

`address(this).balance` is a **global contract balance** — it carries no attribution to which user deposited it. Any ETH sitting in the router at the time of a WETH-input swap is consumed first, before pulling from the actual payer.

ETH can be stranded in the router because `multicall` is `payable`: [2](#0-1) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks **direct** ETH transfers; it does **not** block ETH arriving as `msg.value` on a `payable` function call like `multicall`. ETH sent via `multicall{value: X}(...)` is held by the contract for the duration of the call and remains in `address(this).balance` after the call if `refundETH` is not included. [3](#0-2) 

### Impact Explanation

**User A loses ETH; User B's WETH requirement is silently reduced.**

Step-by-step:

1. User A calls `router.multicall{value: 1 ether}([exactInputSingle(tokenIn=token1, ...)])`. The swap settles via ERC-20 `transferFrom`; the 1 ETH is never consumed. No `refundETH` is included. After the call, `address(router).balance == 1 ether`.

2. User B calls `router.exactInputSingle(tokenIn=WETH, amountIn=2 ether)` with only 1 WETH approved.

3. The pool calls `metricOmmSwapCallback`, which calls `_justPayCallback`: [4](#0-3) 

4. Inside `pay(WETH, userB, pool, 2 ether)`: `nativeBalance = 1 ether`, `value = 2 ether`. The `else if (nativeBalance > 0)` branch fires:
   - 1 ETH is wrapped and transferred to the pool (User A's ETH)
   - Only `2 - 1 = 1 WETH` is pulled from User B via `transferFrom`

5. User A's 1 ETH is permanently destroyed from their perspective — it has been wrapped and delivered to the pool on behalf of User B. User B's swap succeeds with only 1 WETH approved instead of 2.

This is a **direct, irreversible loss of User A's principal** with no recourse.

### Likelihood Explanation

- `multicall` is the standard entry point for ETH-input swaps (as shown in tests and the interface design).
- Omitting `refundETH` is a realistic user/integrator mistake, especially since the `receive()` guard creates a false impression that ETH cannot be accidentally deposited.
- No front-running or special timing is required; the attacker (User B) simply submits a WETH swap after User A's ETH is stranded. Any WETH swap by any user will silently consume the stranded ETH.
- The stranded ETH persists indefinitely until consumed or reclaimed.

### Recommendation

Track per-call ETH attribution using transient storage (EIP-1153), or snapshot `address(this).balance` at the start of each top-level entry point and restrict `pay` to only use the delta introduced by the current call. Alternatively, enforce that `pay` only uses ETH equal to `msg.value` of the current call context, not the entire contract balance.

A simpler mitigation: in `pay`, only use native ETH when `payer == address(this)` (i.e., mid-path), and always pull WETH via `transferFrom` from external payers, leaving ETH-wrapping exclusively to the user's own `multicall` context with explicit `refundETH`.

### Proof of Concept

```solidity
// 1. User A strands 1 ETH in the router
vm.prank(userA);
router.multicall{value: 1 ether}(
    [abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
        tokenIn: address(token1),   // NOT WETH — ETH is never used
        tokenOut: address(token2),
        amountIn: 1000,
        ...
    })))]
);
// router now holds 1 ETH; userA.balance reduced by 1 ETH permanently

// 2. User B swaps WETH->X with only 1 WETH approved (needs 2)
weth.approve(address(router), 1 ether);  // only 1 WETH approved
vm.prank(userB);
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: address(weth),
    amountIn: 2 ether,
    ...
}));
// pay() uses 1 ETH (userA's) + pulls 1 WETH from userB
// swap succeeds; userA's 1 ETH is gone
assertEq(address(router).balance, 0);
assertEq(weth.balanceOf(userB), initialWeth - 1 ether); // only 1 WETH pulled, not 2
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
