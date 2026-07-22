### Title
Router `pay()` consumes entire native ETH balance for WETH settlement, enabling theft of residual ETH from prior callers — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` — the router's **total** native ETH balance — when settling a WETH-input hop. This balance is not scoped to the current call's `msg.value`. Any native ETH left on the router from a prior caller who did not invoke `refundETH()` is silently consumed to pay a subsequent caller's WETH obligation, constituting direct theft of the prior caller's funds.

---

### Finding Description

In `PeripheryPayments.pay()`, the WETH branch reads the router's full native balance: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← entire router balance, not msg.value
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

This branch is reached in `exactInput` for hop 0 whenever `params.tokens[0] == WETH` and `payer == msg.sender`: [2](#0-1) 

The router is `payable` on all entry points (`exactInput`, `exactInputSingle`, `exactOutput`, `exactOutputSingle`, `multicall`). A caller who sends `msg.value` but does not call `refundETH()` leaves native ETH on the router. The `receive()` guard only blocks direct ETH sends from non-WETH addresses; it does not prevent ETH accumulation from payable calls. [3](#0-2) 

Because `address(this).balance` is not scoped to the current transaction's `msg.value`, any residual ETH is freely available to the next caller who routes through a WETH-input pool.

Additionally, `_validatePath` only enforces array-length consistency; it does **not** verify that `params.tokens[i]` matches the actual input token of `params.pools[i]`: [4](#0-3) 

The interface explicitly documents this as the caller's off-chain obligation: [5](#0-4) 

This means an attacker can freely supply `params.tokens[0] = WETH` for any pool whose actual input token is WETH, and the `pay()` function will consume the router's native balance without pulling from the attacker's wallet.

---

### Impact Explanation

**Direct loss of user funds.** Any native ETH stranded on the router (from any prior payable call where `refundETH()` was not invoked) is consumed to settle a subsequent caller's WETH swap obligation. The attacker receives output tokens without providing any ETH or WETH. The victim's ETH is permanently lost.

---

### Likelihood Explanation

**Medium-High.** The `multicall` pattern is the standard way to compose `exactInput` + `refundETH`. Users who call `exactInput` directly with `msg.value > amountIn` (e.g., to avoid computing the exact WETH amount off-chain) will routinely leave residual ETH. An attacker can monitor `address(router).balance` and exploit it atomically in the same block.

---

### Recommendation

Track the ETH available for the current call separately from the router's total balance. One approach: pass the current call's `msg.value` (or a decreasing counter) into `pay()` and consume only from that budget, falling back to `safeTransferFrom` for any remainder. Alternatively, record `address(this).balance` at the start of each entry point and use only the delta attributable to `msg.value`.

---

### Proof of Concept

```
1. Alice calls exactInput(tokens=[WETH, USDC], pools=[wethUsdcPool], amountIn=0.5e18)
   with msg.value = 1e18.
   → pay(WETH, Alice, pool, 0.5e18): nativeBalance=1e18 ≥ 0.5e18 → wraps 0.5 ETH, pays pool.
   → Alice receives USDC. Remaining 0.5 ETH stays on router (Alice forgot refundETH).

2. Attacker calls exactInput(tokens=[WETH, USDC], pools=[wethUsdcPool], amountIn=0.5e18)
   with msg.value = 0, zero WETH approval.
   → pay(WETH, Attacker, pool, 0.5e18): nativeBalance=0.5e18 ≥ 0.5e18 → wraps Alice's ETH, pays pool.
   → Attacker receives USDC. Alice's 0.5 ETH is stolen.
```

The attacker needs only a valid factory-registered WETH pool and knowledge that the router holds residual ETH. No privileged access, no malicious pool, no non-standard token behavior is required.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L235-245)
```text
  function _validatePath(address[] calldata tokens, address[] calldata pools, bytes[] calldata extensionDatas)
    internal
    pure
  {
    if (
      tokens.length < 2 || pools.length != tokens.length - 1 || extensionDatas.length != pools.length
        || pools.length > MAX_PATH_POOLS
    ) {
      revert InvalidPath();
    }
  }
```

**File:** metric-periphery/contracts/interfaces/IMetricOmmSimpleRouter.sol (L11-14)
```text
/// @dev Scope: ERC-20 routes only. No native ETH, WETH wrap/unwrap, on-chain quotes, sweep, or refund helpers.
///      Only pools registered on the configured factory may be used. Path token connectivity and single-hop
///      tokenIn / tokenOut against pool immutables remain the caller's obligation off-chain.
///      `pools[i]` is intended to connect `tokens[i]` and `tokens[i+1]`; `extensionDatas[i]` is passed to `pools[i]`.
```
