### Title
Stranded Router ETH Silently Consumed by Subsequent WETH Payers, Causing Direct Fund Loss - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

### Summary

The `pay()` function in `PeripheryPayments.sol` reads `address(this).balance` — the router's **entire** native ETH balance — and uses it to cover any WETH payment, regardless of which user deposited that ETH. When ETH is stranded on the router (e.g., from a `multicall{value}` call that omits `refundETH`, or an exact-output swap that sends excess ETH), a subsequent user's WETH swap silently consumes that stranded ETH, stealing it from the original depositor. Additionally, `refundETH()` has no access control and sends the full router ETH balance to `msg.sender`, giving any caller a direct path to claim stranded ETH.

### Finding Description

`PeripheryPayments.pay()` handles WETH payments with this logic:

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

`address(this).balance` is the **total** ETH held by the router, with no per-user attribution. Any ETH stranded from a prior call is silently consumed to subsidize the current payer.

ETH is stranded on the router in two documented patterns:

1. `multicall{value: X}([exactInputSingle(amountIn=Y)])` where `Y < X` and no `refundETH` call is included — the test `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` explicitly demonstrates this residue.
2. `exactOutputSingle{value: X}(...)` where `X` exceeds the actual `amountIn` computed by the pool — the user sends a conservative ETH estimate and the excess is not automatically returned.

Once stranded, the ETH is consumed by the next caller whose `pay()` branch hits `nativeBalance > 0`.

`refundETH()` compounds this:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

No access control. Any address can call `refundETH()` and receive the entire router ETH balance, including ETH deposited by other users.

### Impact Explanation

**Direct loss of user principal.** User A's stranded ETH is either:
- Silently consumed by User B's WETH swap (User B pays less than owed; User A's ETH is gone), or
- Directly stolen by any caller via `refundETH()`.

In the `pay()` path, User B's `amountIn` reported back to them is the full `value` (the pool received the correct amount), but User A's ETH funded part of it. User A has no recourse.

### Likelihood Explanation

The stranding condition is a normal user error that the protocol's own documentation and tests anticipate (the `refundETH` call in multicall is optional, not enforced). Any user who:
- Sends excess ETH in an exact-output WETH swap, or
- Omits `refundETH` from a multicall with ETH input

will have their ETH exposed. An attacker monitoring the mempool or router balance can front-run or immediately follow with `refundETH()` or a WETH swap to claim it.

### Recommendation

1. **`pay()`**: Track per-caller ETH attribution in transient storage. Only consume `address(this).balance` up to the amount the current `msg.sender` deposited in this call context (i.e., `msg.value` forwarded through `multicall`). Do not consume ETH deposited by prior callers.

2. **`refundETH()`**: Restrict refunds to the amount the current caller deposited in the current transaction, not the entire router balance. One approach: store `msg.value` per caller in transient storage at `multicall` entry and only refund that amount.

### Proof of Concept

```
1. User A calls:
   router.multicall{value: 2 ether}([
     exactInputSingle(tokenIn=WETH, amountIn=1 ether, ...)
     // no refundETH call
   ])
   → 1 ether is consumed by the swap; 1 ether remains on the router.

2. Attacker calls:
   router.refundETH()
   → address(this).balance = 1 ether → transferred to attacker.
   User A loses 1 ether.

Alternative (pay() path):
2b. Attacker calls:
   router.exactInputSingle(tokenIn=WETH, amountIn=1 ether, ...)
   → In metricOmmSwapCallback, pay(WETH, attacker, pool, 1 ether) is called.
   → nativeBalance = 1 ether >= value = 1 ether
   → Router wraps User A's 1 ether and sends to pool.
   → Attacker pays 0 WETH from their own balance.
   User A loses 1 ether; attacker receives the swap output for free.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L57-63)
```text
  /// @inheritdoc IPeripheryPayments
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
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
