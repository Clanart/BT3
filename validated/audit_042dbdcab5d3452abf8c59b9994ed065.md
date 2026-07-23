### Title
`pay()` uses `address(this).balance` instead of per-call ETH, allowing any WETH-swap caller to drain leftover ETH deposited by prior users — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` resolves the WETH payment leg by reading `address(this).balance` — the router's **entire** native ETH balance — rather than only the ETH that was forwarded with the current call. Because every swap entry-point is `payable` and ETH can accumulate between transactions, any subsequent WETH-input swap will silently consume a prior user's leftover ETH instead of pulling WETH from the actual payer, causing direct loss of the prior user's funds.

---

### Finding Description

`PeripheryPayments.pay()` handles the WETH token leg as follows:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← entire contract balance
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
``` [1](#0-0) 

The branch `nativeBalance >= value` wraps and forwards the contract's ETH **without verifying it belongs to the current caller**. ETH accumulates in the router whenever:

1. A user calls any `payable` entry-point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) with `msg.value` that exceeds the actual swap cost, and omits the `refundETH()` tail call.
2. A user sends ETH with a non-WETH swap (ETH is accepted by the `payable` function but never consumed).

The `receive()` guard only blocks direct ETH sends from non-WETH addresses; it does **not** prevent ETH from entering via the `payable` swap functions. [2](#0-1) 

Once ETH is stranded in the router, the next caller who initiates a WETH-input swap triggers `pay()` with `token == WETH`. Because `address(this).balance >= value`, the branch wraps the stranded ETH and delivers WETH to the pool — the legitimate payer's WETH allowance is never touched. The attacker pays nothing; the prior user's ETH is consumed.

This is the direct analog of the StabilityPool bug: just as `raacToken.balanceOf(address(this))` in `calculateRaacRewards` includes tokens deposited via `depositRAACFromPool` (a separate, unrelated source), `address(this).balance` in `pay()` includes ETH deposited by unrelated prior callers, causing the wrong party to bear the cost.

---

### Impact Explanation

**Direct loss of user principal.** A victim who sends excess ETH (or ETH with a non-WETH swap) and omits `refundETH()` loses that ETH to the next WETH-swap caller. The attacker receives a fully subsidised swap: they pay zero WETH yet receive the full output token amount. The pool's accounting is internally consistent (it receives the correct WETH amount), so the loss is entirely borne by the victim.

---

### Likelihood Explanation

- `multicall` is the standard usage pattern; users routinely batch `exactInputSingle + refundETH`. A dropped or mis-ordered `refundETH` call leaves ETH in the router.
- Any WETH-input swap with `msg.value` slightly above the oracle-quoted cost (e.g., to handle price movement) leaves a dust remainder.
- An attacker can monitor the mempool or the router's ETH balance on-chain and front-run or immediately follow any transaction that leaves ETH behind.
- No special role or permission is required; any unprivileged address can call `exactInputSingle` with `tokenIn = WETH`.

---

### Recommendation

Track only the ETH forwarded with the **current call** by passing `msg.value` (or a per-call snapshot) into `pay()` rather than reading `address(this).balance`:

```diff
- function pay(address token, address payer, address recipient, uint256 value) internal {
+ function pay(address token, address payer, address recipient, uint256 value, uint256 availableNative) internal {
      if (payer == address(this)) {
          IERC20(token).safeTransfer(recipient, value);
      } else if (token == WETH) {
-         uint256 nativeBalance = address(this).balance;
+         uint256 nativeBalance = availableNative;
          ...
      }
  }
```

Pass `msg.value` from each entry-point down through the callback context (transient storage), decrementing it as ETH is consumed. Alternatively, adopt the Uniswap v3 pattern of storing `msg.value` in a transient slot at entry and zeroing it after use, so `pay()` can never spend more ETH than the current caller provided.

---

### Proof of Concept

**Setup:** Router deployed with WETH address. Two users, Alice and Bob.

1. **Alice** calls `exactInputSingle{value: 2 ether}(params)` where `params.tokenIn = WETH` and the swap only costs `1 ether`. She omits `refundETH()`. The router now holds `1 ether` of Alice's ETH.

2. **Bob** calls `exactInputSingle{value: 0}(params)` where `params.tokenIn = WETH` and the swap costs `1 ether`. Bob has approved the router for 1 WETH but sends no ETH.

3. Inside `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, Bob, pool, 1e18)`:
   - `address(this).balance == 1 ether` (Alice's leftover)
   - Branch `nativeBalance >= value` is taken
   - `IWETH9(WETH).deposit{value: 1 ether}()` — Alice's ETH is wrapped
   - `IERC20(WETH).safeTransfer(pool, 1e18)` — pool receives WETH
   - Bob's WETH allowance is **never touched**

4. Bob receives the full swap output. Alice's 1 ETH is gone. [3](#0-2) [4](#0-3)

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
