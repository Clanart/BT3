### Title
Stranded Router ETH Consumed by Subsequent WETH Swap, Stealing Prior Depositor's Funds — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay` function's WETH hybrid path unconditionally consumes the router's entire native ETH balance to partially fund any WETH swap, without verifying that the ETH belongs to the current caller. Any ETH left in the router from a prior transaction is silently stolen by the next user who swaps with WETH as `tokenIn`.

---

### Finding Description

`PeripheryPayments.pay` has three branches for `token == WETH`: [1](#0-0) 

The middle branch (lines 78–81) fires whenever `0 < address(this).balance < value`. It wraps **all** of the router's native ETH and sends it to the pool, then pulls only the remainder from `payer` via `transferFrom`. There is no check that the ETH in the router was deposited by the current `payer`.

ETH can be stranded in the router because every swap entry-point is `payable` and the `receive()` guard only blocks direct transfers, not `msg.value` attached to function calls: [2](#0-1) 

The protocol's own test suite demonstrates the stranding risk — it explicitly requires users to bundle `refundETH()` in the same `multicall` to reclaim excess ETH: [3](#0-2) 

If a user omits `refundETH()`, the excess ETH sits in the router indefinitely and is available to the hybrid path.

---

### Impact Explanation

**Direct ETH theft.** Suppose User A calls `multicall{value: 2 ether}` with `exactInputSingle(amountIn=1 ether, tokenIn=WETH)` but omits `refundETH()`. The router retains 1 ETH. User B then calls `exactInputSingle(amountIn=2 ether, tokenIn=WETH)`. Inside `pay`:

- `nativeBalance = 1 ETH`, `value = 2 ETH` → hybrid branch fires
- Router wraps User A's 1 ETH and forwards it to the pool
- Only 1 ETH of WETH is pulled from User B

User A loses 1 ETH permanently. User B's WETH cost is halved at User A's expense. The pool receives the correct total, so no pool insolvency occurs, but the payer attribution is corrupted: User A's ETH funds User B's swap.

---

### Likelihood Explanation

The stranding precondition is realistic:

1. `multicall` is the standard ETH-input pattern; users who send excess ETH without `refundETH()` strand it.
2. Any payable swap function called directly with `msg.value > amountIn` (e.g., `exactInputSingle{value: 2 ether}(amountIn=1 ether)`) strands the excess.
3. An attacker can monitor the mempool or router balance and immediately follow with a WETH swap to drain it.

---

### Recommendation

Track only the ETH that the **current caller** explicitly sent in this transaction. The standard fix is to compare `msg.value` against the amount consumed and use only that portion in `pay`, or to zero out the router's ETH balance at the start of each top-level call and revert if it is non-zero (enforcing that no stale ETH exists). Alternatively, `pay` should only use `msg.value` (passed as a parameter) rather than `address(this).balance`, so it cannot accidentally consume ETH from a prior transaction.

---

### Proof of Concept

```solidity
// 1. User A strands 1 ETH in the router (forgot refundETH)
vm.deal(userA, 2 ether);
vm.prank(userA);
router.exactInputSingle{value: 2 ether}(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        tokenIn: address(weth), amountIn: 1 ether, ...
    })
);
// router now holds 1 ETH

assertEq(address(router).balance, 1 ether);

// 2. User B swaps 2 WETH; hybrid path consumes User A's 1 ETH
uint256 wethBefore = weth.balanceOf(userB);
vm.prank(userB);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        tokenIn: address(weth), amountIn: 2 ether, ...
    })
);

// User B only spent 1 WETH (not 2), funded by User A's stranded ETH
assertEq(wethBefore - weth.balanceOf(userB), 1 ether); // discount
assertEq(address(router).balance, 0);                  // User A's ETH gone
```

The pool receives the full 2 ETH worth of WETH (1 from router ETH + 1 from User B's WETH), so the swap succeeds. User A's 1 ETH is irrecoverably consumed.

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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```
