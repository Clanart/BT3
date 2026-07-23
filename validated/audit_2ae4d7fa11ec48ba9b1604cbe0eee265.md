### Title
Stranded Native ETH from Excess `msg.value` in WETH Swap Functions Is Permanently Claimable by Any Caller via `refundETH()` / `pay()` — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` function in `PeripheryPayments.sol` uses `address(this).balance` — the router's **entire** native ETH balance — when settling a WETH swap obligation, with no per-user attribution. Because every payable swap entry-point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`) accepts `msg.value` but never automatically refunds excess ETH, any ETH left over after a WETH swap is permanently stranded on the router. A subsequent unprivileged caller can then either (a) call the public `refundETH()` to steal the stranded ETH directly, or (b) call any WETH swap with `msg.value = 0` and have the router silently consume the victim's stranded ETH to settle the attacker's obligation — giving the attacker a free swap.

---

### Finding Description

**Root cause — `pay()` uses the router's global ETH balance, not the current caller's ETH:** [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← entire router balance
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value); // payer charged nothing
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

When `nativeBalance >= value`, the payer is charged **zero** WETH; the router's entire ETH balance is consumed. There is no check that this ETH was deposited by the current payer in the current transaction.

**Stranding mechanism — payable swap functions never auto-refund excess ETH:**

`exactInputSingle`, `exactOutputSingle`, `exactInput`, and `exactOutput` are all `external payable` and call `_clearExpectedCallbackPool()` on success but never call `refundETH()`. [2](#0-1) 

The most realistic stranding path is `exactOutputSingle` with WETH as `tokenIn`: the user must send up to `amountInMaximum` ETH because the exact input is unknown before the swap executes. The actual input is almost always less than `amountInMaximum`, leaving the difference stranded.

**Theft mechanism — `refundETH()` has no access control:** [3](#0-2) 

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);  // ← any caller, entire balance
    }
}
```

Any address can call `refundETH()` at any time and receive the router's full ETH balance.

---

### Impact Explanation

**Direct loss of user principal — High severity.**

- **Attack vector 1 (direct ETH theft):** Attacker monitors the router for a non-zero ETH balance (e.g., via `eth_getBalance`). As soon as a victim's excess ETH is stranded, the attacker calls `refundETH()` and receives 100 % of the stranded amount.
- **Attack vector 2 (free WETH swap):** Attacker calls `exactInputSingle(WETH→TokenX, amountIn = stranded_amount)` with `msg.value = 0`. `pay()` finds `address(this).balance >= value`, wraps the victim's ETH, and transfers WETH to the pool — the attacker receives `TokenX` without spending any ETH or WETH.

In both cases the victim loses 100 % of their excess ETH. For `exactOutputSingle` with a large `amountInMaximum` buffer (common in production integrations), the stranded amount can be substantial.

---

### Likelihood Explanation

- **Trigger is a standard user pattern.** Sending `amountInMaximum` ETH with `exactOutputSingle` is the documented and tested usage pattern (the test suite itself demonstrates it with `refundETH` in a multicall).
- **Forgetting `refundETH` is easy.** Users calling swap functions directly (not via multicall) have no automatic refund. The `receive()` guard prevents accidental ETH deposits but does not prevent excess `msg.value` from payable swap calls.
- **Attacker requires zero privilege.** `refundETH()` is `external` with no `onlyOwner` or caller check. Any EOA or contract can call it in the same block as the victim's transaction.
- **MEV-extractable.** A searcher can bundle `refundETH()` immediately after any transaction that leaves ETH on the router. [4](#0-3) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) prevents direct ETH deposits but does **not** prevent excess `msg.value` from payable function calls, so ETH stranding is reachable through normal usage.

---

### Recommendation

**Short term:**
1. In `pay()`, when `token == WETH` and `payer != address(this)`, do **not** consume `address(this).balance` unless the caller explicitly opted in (e.g., via a flag or by routing through a dedicated native-ETH wrapper). Alternatively, track the ETH deposited by the current call frame and only consume that amount.
2. Add a `msg.value == 0` assertion in non-WETH swap paths to prevent accidental ETH stranding.

**Long term:**
1. Restrict `refundETH()` so it can only be called as part of a `multicall` originating from the same `msg.sender` who deposited the ETH, or remove it as a standalone external function.
2. Require that any payable swap function consuming native ETH explicitly refunds the remainder before returning, rather than relying on the caller to append a `refundETH()` step.

---

### Proof of Concept

```
Setup:
  - Router deployed with WETH and Factory
  - Pool: WETH / TokenB registered in Factory
  - Victim (Alice) has 2 ETH
  - Attacker (Bob) has 0 ETH

Step 1 — Alice strands ETH:
  Alice calls router.exactOutputSingle{value: 1 ether}(
      pool=pool, tokenIn=WETH, tokenOut=TokenB,
      zeroForOne=true, amountOut=500, amountInMaximum=1 ether,
      recipient=alice, deadline=..., priceLimitX64=0, extensionData=""
  )
  → Pool executes swap, actual WETH input = 600 (600 wei)
  → pay(WETH, alice, pool, 600):
      nativeBalance = 1 ether ≥ 600 → wraps 600 wei ETH, sends WETH to pool
  → Router ETH balance = 1 ether - 600 = 999999999999999400 wei (stranded)
  → Alice receives 500 TokenB; Alice's ETH balance = 1 ether (unchanged — she sent 1 ether via msg.value)

Step 2 — Bob steals stranded ETH:
  Bob calls router.refundETH()
  → balance = 999999999999999400 wei
  → _transferETH(bob, 999999999999999400)
  → Bob receives ~1 ETH for free

Result:
  Alice lost ~1 ETH (the excess msg.value she sent)
  Bob gained ~1 ETH with zero capital
```

The `pay()` call at line 75–77 is the exact site where the victim's stranded ETH is silently consumed on behalf of any subsequent WETH payer, and `refundETH()` at line 58–63 is the direct theft path. [5](#0-4) [6](#0-5) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
