### Title
Unattributed Router ETH Balance Allows Any Caller to Drain Stranded Native ETH via WETH Payment Path — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` function in `PeripheryPayments.sol` uses `address(this).balance` — the router's entire native ETH balance — to settle WETH swap obligations without any per-user attribution. If ETH is stranded on the router from a prior transaction (a user sent excess `msg.value` and omitted `refundETH()`), any subsequent caller who initiates a WETH swap with `msg.value = 0` has their pool obligation silently covered by the victim's stranded ETH. The victim loses principal; the attacker receives a free swap.

---

### Finding Description

`PeripheryPayments.pay()` contains a WETH-specific branch: [1](#0-0) 

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

`address(this).balance` is the router's **persistent** native ETH balance — it is not scoped to the current transaction's `msg.value` and is not attributed to any caller. ETH accumulates on the router whenever a user calls a `payable` entry point (e.g., `exactInputSingle`) with `msg.value` exceeding the swap's actual WETH cost and omits `refundETH()` from their multicall.

The `receive()` guard only blocks plain ETH transfers: [2](#0-1) 

It does **not** prevent ETH from accumulating via `msg.value` in payable function calls. Once stranded, that ETH is available to `pay()` for any caller's WETH obligation in any future transaction.

The callback that invokes `pay()` is: [3](#0-2) 

The `payer` argument is `_getPayer()` — the original swap initiator — but the ETH actually consumed comes from `address(this).balance`, which may belong to a different user entirely.

---

### Impact Explanation

**Direct loss of user principal.** A victim who strands ETH on the router loses it to any attacker who subsequently executes a WETH swap. The attacker pays zero ETH (`msg.value = 0`) and zero WETH (no `transferFrom` is triggered when `nativeBalance >= value`), receiving the full swap output at the victim's expense. The loss equals the ETH consumed from the router's balance, bounded only by the attacker's chosen `amountIn`.

---

### Likelihood Explanation

**Medium.** The precondition — ETH stranded on the router — arises whenever a user:
1. Calls any `payable` swap entry point with `msg.value` exceeding the WETH cost, **and**
2. Omits `refundETH()` from their multicall.

The interface NatDoc explicitly states "No native ETH … or refund helpers" in scope: [4](#0-3) 

This creates a documentation mismatch: the router is `payable` and does consume native ETH for WETH, but the interface discourages users from expecting ETH handling, making it likely that users will not include `refundETH()`. Once ETH is stranded, exploitation requires only a single unprivileged WETH swap call.

---

### Recommendation

Track per-transaction ETH attribution using transient storage (already used elsewhere in the router via `TransientCallbackPool`). Store the `msg.value` credited to the current caller at entry and deduct only from that attributed balance inside `pay()`. Any unspent attributed ETH should be refunded automatically at the end of the outermost call, or `pay()` should fall through to `safeTransferFrom` when the router's balance cannot be attributed to the current payer.

---

### Proof of Concept

```
Block N — Victim transaction:
  victim calls exactInputSingle(pool, WETH→X, amountIn=100)
    with msg.value = 200
  Callback: pay(WETH, victim, pool, 100)
    → nativeBalance = 200 >= 100
    → wraps 100 ETH, sends WETH to pool ✓
  Transaction ends: router.balance = 100 ETH (stranded, no refundETH called)

Block N+1 — Attacker transaction:
  attacker calls exactInputSingle(pool, WETH→Y, amountIn=100)
    with msg.value = 0, no WETH approval needed
  Callback: pay(WETH, attacker, pool, 100)
    → nativeBalance = 100 >= 100
    → wraps 100 ETH (victim's), sends WETH to pool ✓
    → safeTransferFrom(attacker, ...) is NEVER called
  Attacker receives full swap output; victim's 100 ETH is gone.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmSimpleRouter.sol (L11-13)
```text
/// @dev Scope: ERC-20 routes only. No native ETH, WETH wrap/unwrap, on-chain quotes, sweep, or refund helpers.
///      Only pools registered on the configured factory may be used. Path token connectivity and single-hop
///      tokenIn / tokenOut against pool immutables remain the caller's obligation off-chain.
```
