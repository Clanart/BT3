The `pay` function in `PeripheryPayments.sol` is the critical piece. Let me read the full router to understand how `pay` is called in the swap callback context.

The vulnerability is real and traceable through the code. Here is the full analysis:

---

### Title
Stranded ETH in Router Consumed by Subsequent User's WETH Payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`PeripheryPayments.pay` uses `address(this).balance` — the router's **total** native ETH balance — when settling a WETH-input swap. ETH left in the router by a prior user (who sent ETH via `multicall{value}` for a non-WETH swap and omitted `refundETH`) is silently consumed to cover a later user's WETH obligation, causing direct, permanent ETH loss for the first user.

### Finding Description

`pay` in `PeripheryPayments.sol` has three branches for `token == WETH, payer != address(this)`: [1](#0-0) 

```
nativeBalance = address(this).balance   // ← entire router balance, not per-user
if nativeBalance >= value:
    wrap all, transfer to pool
else if nativeBalance > 0:
    wrap nativeBalance, transfer to pool
    pull (value - nativeBalance) WETH from payer
else:
    pull value WETH from payer
```

There is no per-user accounting. Any ETH sitting in the router — regardless of who deposited it — is treated as available to satisfy the current caller's WETH obligation.

ETH can accumulate in the router because `multicall` is `payable`: [2](#0-1) 

A user who calls `multicall{value: X}([exactInputSingle(token1→token2)])` sends ETH that is never touched by the non-WETH swap path. If they omit `refundETH` as a second call, the ETH is stranded in the router until the next transaction.

The `receive()` guard only blocks direct ETH pushes; it does not prevent ETH from accumulating via `payable` entry points: [3](#0-2) 

### Impact Explanation

**Attack path:**

1. User A calls `multicall{value: 1 ether}([exactInputSingle(token1→token2)])` — the swap uses `token1`, so `pay(token1, userA, pool, amount)` hits the plain `safeTransferFrom` branch. The 1 ETH is never touched and remains in the router.

2. User B calls `exactInputSingle(WETH→token2, amountIn=2 ether)` with only 1 WETH approved.

3. The pool calls `metricOmmSwapCallback`, which calls `_justPayCallback`: [4](#0-3) 

4. Inside `pay(WETH, userB, pool, 2 ether)`: `nativeBalance = 1 ether` (user A's). The `else if (nativeBalance > 0)` branch fires — 1 ETH is wrapped and sent to the pool, then only 1 WETH is pulled from user B. The pool receives the full 2 WETH.

5. User A's 1 ETH is permanently gone. User B's swap succeeds despite having only half the required WETH approved.

**Impact:** Direct, permanent loss of user A's ETH principal. User B (or a bot monitoring the mempool) can exploit this intentionally by submitting a WETH swap sized to consume exactly the stranded ETH, requiring fewer tokens from their own wallet.

### Likelihood Explanation

- Omitting `refundETH` from a `multicall` is a common user mistake, especially when the swap is for a non-WETH token and the user sends ETH speculatively.
- A MEV bot can watch for transactions that leave ETH in the router and immediately follow with a WETH swap to drain it.
- No privileged access, malicious pool, or non-standard token is required.

### Recommendation

Track per-call ETH entitlement in transient storage (set at `multicall` entry, decremented in `pay`) and only allow `pay` to consume ETH up to the current call's entitlement. Alternatively, reject ETH sent to `multicall` when no WETH-input swap is present, or auto-refund any remaining ETH at the end of each `multicall` iteration.

### Proof of Concept

```solidity
// 1. User A strands 1 ETH
vm.prank(userA);
bytes[] memory calls = new bytes[](1); // no refundETH
calls[0] = abi.encodeCall(router.exactInputSingle, ExactInputSingleParams({
    tokenIn: address(token1), tokenOut: address(token2), ...
}));
router.multicall{value: 1 ether}(calls);
assertEq(address(router).balance, 1 ether); // stranded

// 2. User B swaps WETH→token2 with amountIn=2 ether, only 1 WETH approved
weth.approve(address(router), 1 ether); // only 1 WETH
vm.prank(userB);
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: address(weth), tokenOut: address(token2),
    amountIn: 2 ether, ...
}));

// 3. Assertions
assertEq(address(router).balance, 0);       // user A's ETH consumed
assertEq(weth.balanceOf(userB), initialWeth - 1 ether); // userB paid only 1 WETH
// userA's ETH balance permanently reduced by 1 ether with no benefit
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
