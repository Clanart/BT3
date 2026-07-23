Audit Report

## Title
Stranded ETH on the router is silently consumed by any subsequent WETH-input swap caller, enabling theft of prior users' native ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay()` function in `PeripheryPayments.sol` unconditionally uses the router's entire native ETH balance to settle a WETH-input swap before pulling from the declared `payer`. Because there is no per-caller accounting of ETH, any ETH left on the router by a prior user (e.g., excess `msg.value` from a direct `exactInputSingle` call) is silently consumed to settle a completely different caller's swap. The victim loses their ETH; the attacker receives token output without spending any WETH or ETH.

## Finding Description
`PeripheryPayments.pay()` (lines 73–84) handles the WETH branch as follows:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // payer never touched
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

When `nativeBalance >= value`, the `payer` argument is completely ignored. The router wraps its own ETH balance and transfers WETH to the pool on behalf of whoever triggered the callback.

The call path for `exactInputSingle` with `tokenIn == WETH`:

1. `exactInputSingle` (payable) sets `msg.sender` as payer via `_setNextCallbackContext`, then calls `pool.swap`. [2](#0-1) 

2. The pool calls back `metricOmmSwapCallback` → `_justPayCallback`, which calls `pay(WETH, _getPayer(), pool, amount)`. [3](#0-2) 

3. Inside `pay()`, if `address(this).balance >= amount`, the router wraps its ETH and sends WETH to the pool — the `payer` (attacker's `msg.sender`) is never charged. [4](#0-3) 

ETH becomes stranded on the router when a user calls `exactInputSingle{value: X}` directly (not via multicall) with `X > params.amountIn` — the excess has no in-call refund path since `refundETH` is a separate call. The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH from arriving via the `payable` swap entry-points. [5](#0-4) 

## Impact Explanation
Direct loss of user principal. The victim's stranded ETH is consumed to settle a stranger's swap. The attacker receives real token output without spending any WETH or ETH. The loss equals the full amount of stranded ETH, bounded only by what the victim sent. This is a Critical/High direct loss of user principal — the victim's funds are permanently transferred to the attacker's swap output with no recovery path.

## Likelihood Explanation
The trigger requires ETH to be stranded on the router, which is a realistic, non-exotic condition. A user calling `exactInputSingle{value: X}` directly with `X > amountIn` has no mechanism to reclaim the excess in the same call. Once stranded ETH exists, any observer can exploit it in the very next block with a single `exactInputSingle` call specifying WETH as `tokenIn` and `amountIn` equal to the stranded amount — no special permissions, no approvals, no ETH or WETH required from the attacker.

## Recommendation
Restrict the ETH-first payment path to the case where the router is paying from its own balance as an intermediate hop (`payer == address(this)`). For external payers, always pull WETH directly:

```solidity
} else if (token == WETH) {
    if (payer == address(this)) {
        IERC20(WETH).safeTransfer(recipient, value);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

ETH wrapping (depositing `msg.value` as WETH) should be performed explicitly and attributably before the swap call, not implicitly inside the payment callback.

## Proof of Concept
**Setup**: Router deployed with WETH. A WETH/token1 pool exists with liquidity.

**Step 1 — Victim strands ETH:**
```solidity
// Victim sends 1 ETH but only needs 0.5 ETH for the swap (called directly, not via multicall).
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    pool: pool, tokenIn: WETH, tokenOut: token1, zeroForOne: true,
    amountIn: 0.5 ether, amountOutMinimum: 0, recipient: victim,
    deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
}));
// Router now holds 0.5 ETH (stranded). No refundETH was called.
```

**Step 2 — Attacker steals it (zero ETH, zero WETH approval):**
```solidity
router.exactInputSingle(ExactInputSingleParams({
    pool: pool, tokenIn: WETH, tokenOut: token1, zeroForOne: true,
    amountIn: 0.5 ether, amountOutMinimum: 0, recipient: attacker,
    deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
}));
// metricOmmSwapCallback → _justPayCallback → pay(WETH, attacker, pool, 0.5 ETH)
// address(this).balance == 0.5 ETH >= 0.5 ETH → deposits victim's ETH, transfers WETH to pool.
// Attacker receives token1 output. Victim's 0.5 ETH is permanently lost.
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
