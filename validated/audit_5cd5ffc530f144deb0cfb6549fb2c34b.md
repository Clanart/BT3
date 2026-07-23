### Title
Stranded native ETH on the router is silently consumed by any subsequent WETH swap from an unrelated caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments` uses the router's **entire** native ETH balance to fund WETH payments without attributing that balance to the current caller. Any ETH left on the router from a prior transaction (excess `msg.value` not refunded) is silently consumed by the next user who initiates a WETH swap, causing direct, irrecoverable loss of the original depositor's ETH.

---

### Finding Description

`PeripheryPayments.pay()` handles WETH payments as follows: [1](#0-0) 

```solidity
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
        uint256 nativeBalance = address(this).balance;   // ← reads ALL router ETH
        if (nativeBalance >= value) {
            IWETH9(WETH).deposit{value: value}();
            IERC20(WETH).safeTransfer(recipient, value); // ← pays with router ETH, no payer pull
        } else if (nativeBalance > 0) {
            IWETH9(WETH).deposit{value: nativeBalance}();
            IERC20(WETH).safeTransfer(recipient, nativeBalance);
            IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
        } else {
            IERC20(WETH).safeTransferFrom(payer, recipient, value);
        }
    } else { ... }
}
```

The design assumes that any native ETH sitting on the router belongs to the **current** caller (i.e., it arrived as `msg.value` in this transaction). That assumption is wrong. The router is payable in every swap entry-point: [2](#0-1) 

If a caller sends `msg.value > amountIn` and omits `refundETH()`, the surplus ETH remains on the router across transaction boundaries. The `receive()` guard only blocks **direct** ETH pushes; it does **not** prevent ETH from accumulating via `msg.value` in payable function calls: [3](#0-2) 

The next caller who swaps with `tokenIn == WETH` and `msg.value == 0` will have their payment funded entirely from the stranded ETH — no WETH approval or ETH contribution required from them.

---

### Impact Explanation

**Direct loss of user ETH — High severity.**

The stranded ETH is transferred to the pool as WETH on behalf of the attacker's swap. The original depositor has no mechanism to recover it once the subsequent swap executes. The loss is bounded only by the amount of ETH stranded and the attacker's chosen `amountIn`.

---

### Likelihood Explanation

**Medium.** Users routinely send a round-number `msg.value` as a buffer when swapping WETH (e.g., send 1 ETH, swap 0.7 ETH). The correct pattern is to include `refundETH()` in the same `multicall`. Omitting it — whether by user error, a frontend bug, or a failed second call in a multicall — leaves ETH stranded. A MEV bot or any watching attacker can drain it in the very next block with a zero-value WETH swap.

---

### Recommendation

Track the ETH contributed by the **current** caller in transient storage at the start of each payable entry-point and cap `pay()`'s native-ETH consumption to that tracked amount. Alternatively, require `msg.value` to equal exactly the WETH amount needed (and revert otherwise), or remove the native-ETH shortcut entirely and require callers to wrap ETH themselves before calling the router.

---

### Proof of Concept

**Step 1 — Alice strands ETH (realistic user error):**
```solidity
// Alice sends 1 ETH but only swaps 0.5 ETH worth of WETH.
// She forgets refundETH() in her multicall.
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    pool:            pool,
    tokenIn:         WETH,
    tokenOut:        token1,
    zeroForOne:      true,
    amountIn:        0.5 ether,   // pay() consumes 0.5 ETH from router balance
    amountOutMinimum: 0,
    recipient:       alice,
    deadline:        block.timestamp,
    priceLimitX64:   0,
    extensionData:   ""
}));
// 0.5 ETH remains on the router, unattributed.
```

**Step 2 — Bob steals Alice's ETH:**
```solidity
// Bob sends no ETH and has no WETH approval.
// pay() sees nativeBalance = 0.5 ether >= value = 0.5 ether → uses Alice's ETH.
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool:            pool,
    tokenIn:         WETH,
    tokenOut:        token1,
    zeroForOne:      true,
    amountIn:        0.5 ether,   // funded entirely from Alice's stranded ETH
    amountOutMinimum: 0,
    recipient:       bob,
    deadline:        block.timestamp,
    priceLimitX64:   0,
    extensionData:   ""
}));
// Bob receives token1 output; Alice's 0.5 ETH is gone.
```

**Root cause line:** [4](#0-3) 

`address(this).balance` is read without any per-caller attribution, so any ETH on the router — regardless of who deposited it — is available to fund the current swap.

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
