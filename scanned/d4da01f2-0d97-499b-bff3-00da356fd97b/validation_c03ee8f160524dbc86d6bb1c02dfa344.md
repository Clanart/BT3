### Title
Unattributed Router ETH Balance Consumed by Any WETH Swap, Enabling Theft of Stranded User Funds — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` function in `PeripheryPayments.sol` uses the router's native ETH balance to settle WETH swap payments without attributing that ETH to the specific user who deposited it. Any ETH stranded on the router from a prior `multicall{value: ...}` call where the user omitted `refundETH()` can be silently consumed by any subsequent WETH swap, allowing an attacker to receive a fully or partially subsidized swap at the victim's expense.

---

### Finding Description

The `pay()` function contains a WETH-specific payment path that checks the router's native ETH balance before pulling from the declared payer: [1](#0-0) 

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

There is **no attribution**: the router does not track which user deposited which ETH. Any ETH present on the router is consumed by the next WETH payment, regardless of who the declared `payer` is.

ETH arrives on the router via `multicall{value: X}(...)` because `multicall` is `payable`: [2](#0-1) 

The `receive()` guard only blocks direct ETH transfers from non-WETH addresses; it does not block ETH attached to a `multicall` call: [3](#0-2) 

If a user calls `multicall{value: 1 ETH}([exactInputSingle(WETH→token1, amountIn=0.5 ETH)])` without appending `refundETH()`, the swap consumes 0.5 ETH (deposited as WETH and forwarded to the pool) and the remaining 0.5 ETH is silently left on the router.

An attacker monitoring the router's ETH balance can then call `exactInputSingle(WETH→token1, amountIn=0.5 ETH)` without sending any ETH or holding any WETH allowance. The callback path reaches `_justPayCallback`: [4](#0-3) 

which calls `pay(WETH, attacker, pool, 0.5e18)`. Since `nativeBalance (0.5 ETH) >= value (0.5e18)`, the router deposits the **victim's** 0.5 ETH as WETH and forwards it to the pool. The attacker receives token1 output and pays nothing.

The payer stored in transient context is the attacker's address, but the `nativeBalance >= value` branch never calls `safeTransferFrom(payer, ...)` — it only uses the router's own ETH balance — so the attacker's address is irrelevant to the actual payment: [5](#0-4) 

The same theft applies to the partial-subsidy branch (`0 < nativeBalance < value`): the attacker pays only `value − nativeBalance` instead of the full `value`, with the difference coming from the victim's stranded ETH.

---

### Impact Explanation

Direct loss of user principal. The victim's ETH is consumed by the attacker's swap. The attacker receives the full swap output without paying any input. The loss is bounded only by the amount of ETH stranded on the router, which can be arbitrarily large depending on the victim's transaction size.

---

### Likelihood Explanation

The `multicall` pattern is the standard way to compose permit + swap + refund steps. Users who omit `refundETH()` — a common mistake, especially when integrating via SDK or copy-paste — leave ETH on the router. An attacker can monitor the router's ETH balance on-chain and immediately follow any transaction that leaves ETH behind. No special permissions, approvals, or privileged access are required; a plain `exactInputSingle` call with `tokenIn = WETH` suffices.

---

### Recommendation

Track ETH attribution per-caller in transient storage (analogous to how `TransientCallbackPool` tracks the payer and token). Only allow the ETH deposited by the current `msg.sender` to be used for their own WETH payment in the same transaction. Alternatively, remove the native-ETH-first logic from `pay()` entirely and require callers to wrap ETH themselves (e.g., via a dedicated `wrapETH` multicall step) before invoking the router.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-87)
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
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
