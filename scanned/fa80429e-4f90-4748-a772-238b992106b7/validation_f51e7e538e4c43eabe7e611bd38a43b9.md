Looking at the `pay` function in `PeripheryPayments.sol` and how it is invoked from the swap and liquidity callbacks, there is a direct analog to M-01.

---

### Title
Stale ETH balance in `pay()` allows any caller to drain leftover ETH from prior users when WETH is the swap/liquidity token — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` (the router's total ETH balance) rather than only the ETH sent by the current caller when `token == WETH`. If a prior user leaves ETH in the router (e.g., by overpaying and omitting `refundETH()` from their multicall), any subsequent caller swapping or adding liquidity with WETH can have their payment covered by that stale ETH. The `payer` parameter — which is set to `msg.sender` of the original swap/liquidity call — is silently bypassed.

### Finding Description

`PeripheryPayments.pay()` contains the following WETH branch: [1](#0-0) 

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

When `nativeBalance >= value`, the `payer` argument is **completely ignored**. The function wraps and forwards whatever ETH happens to be sitting in the contract, regardless of who sent it.

`pay()` is called from `_justPayCallback`, which is triggered during every single-hop swap: [2](#0-1) 

The payer stored in transient context is always `msg.sender` of the original swap call: [3](#0-2) 

The same `pay()` path is used in `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback`: [4](#0-3) 

Both contracts expose a `payable multicall`: [5](#0-4) [6](#0-5) 

There is no enforcement that `msg.value` equals `amountIn` when `tokenIn == WETH`, and no enforcement that `refundETH()` is included in every multicall. ETH sent in excess of what the swap consumes silently remains in the contract. [7](#0-6) 

### Impact Explanation

**Direct loss of user ETH principal.** A user who sends ETH to the router (e.g., overpays `msg.value` relative to `amountIn`, or whose swap partially fills due to a price limit) and does not include `refundETH()` in their multicall permanently loses the excess ETH to the next WETH swapper. The attacker pays nothing (`msg.value = 0`) and the pool is settled entirely from the victim's leftover balance.

### Likelihood Explanation

**Medium.** The trigger requires a prior user to leave ETH in the contract. This is realistic because:
- `exactInputSingle` and `exactOutput` are `payable` with no `msg.value == amountIn` guard.
- `refundETH()` is optional and not enforced by the router.
- Integrators and new users commonly overpay ETH and omit the refund step.
- The attacker needs only to observe a non-zero ETH balance on the router (trivially visible on-chain) and call `exactInputSingle(WETH, amountIn ≤ balance, msg.value = 0)`.

### Recommendation

Track the ETH contributed by the current caller in a transient storage slot (e.g., store `msg.value` at entry to each payable function) and cap the native-ETH branch of `pay()` to that tracked amount rather than `address(this).balance`. Alternatively, enforce `msg.value == amountIn` when `tokenIn == WETH` in each swap entry point, and revert if `msg.value > 0` when `tokenIn != WETH`.

### Proof of Concept

1. **Setup**: WETH pool exists; router has `address(this).balance == 0`.
2. **Victim (User A)**: calls `exactInputSingle({tokenIn: WETH, amountIn: 50 ether, ...})` with `msg.value = 100 ether` (overpays). The swap callback calls `pay(WETH, userA, pool, 50 ether)`. `nativeBalance = 100 ether >= 50 ether`, so 50 ETH is wrapped and sent to the pool. **50 ETH remains in the router.** User A does not call `refundETH()`.
3. **Attacker (User B)**: observes `address(router).balance == 50 ether`. Calls `exactInputSingle({tokenIn: WETH, amountIn: 50 ether, ...})` with `msg.value = 0`. Payer is set to `userB` in transient storage. The swap callback calls `pay(WETH, userB, pool, 50 ether)`. `nativeBalance = 50 ether >= 50 ether` → User A's ETH is wrapped and forwarded to the pool. User B's swap settles in full.
4. **Result**: User A loses 50 ETH. User B receives swap output without paying anything. The `payer = userB` stored in transient context is never consulted.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L42-47)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
