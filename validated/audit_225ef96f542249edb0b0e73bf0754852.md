### Title
Stranded ETH in router is consumed by any subsequent WETH-input swap, stealing victim's native ETH — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay` helper in `PeripheryPayments` uses `address(this).balance` — the router's **entire, unpartitioned** native ETH balance — to decide how much ETH to wrap and forward when settling a WETH payment. Because this balance is shared across all callers and transactions, ETH left in the router from any prior user's call can be silently consumed by a subsequent attacker's WETH swap, causing direct loss of the victim's ETH.

---

### Finding Description

When `token == WETH`, `pay` reads the router's live ETH balance and, if it covers the required amount, wraps and forwards it without pulling anything from `payer`: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // payer is never touched
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

The router accepts ETH through every `payable` entry point — `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`, `unwrapWETH9`, `sweepToken`, `refundETH`, and all `selfPermit*` variants. [2](#0-1) 

The `receive()` guard only blocks **direct** ETH sends (no calldata); it does **not** block ETH attached to any of those payable function calls: [3](#0-2) 

ETH sent with a non-WETH swap, or excess ETH sent with a WETH swap, remains in the router until `refundETH` is explicitly called. Because `address(this).balance` is never partitioned per-user or per-call, any ETH stranded from User A's transaction is indistinguishable from ETH an attacker legitimately sent. An attacker who calls `exactInputSingle` with `tokenIn = WETH` and `msg.value = 0` will have the router consume User A's stranded ETH to settle the swap, paying nothing themselves.

The same `pay` function is invoked from both the router's `_justPayCallback` and `_exactOutputIterateCallback`, and from `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback` when token0 or token1 is WETH: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**Direct loss of user principal (native ETH).** The victim loses ETH they sent to the router; the attacker receives a WETH swap settled entirely at the victim's expense. The loss is bounded by the amount of ETH stranded in the router at the time of the attack, which can equal the full `amountIn` of a prior WETH swap — an unbounded, user-controlled amount.

---

### Likelihood Explanation

ETH stranding is a **normal operational condition**: users routinely send excess ETH with WETH swaps (to avoid computing the exact amount off-chain) and rely on a subsequent `refundETH` call to recover the surplus. Any window between the swap and the `refundETH` call — including separate transactions, failed multicall legs, or simple user error — leaves ETH exploitable. An attacker can monitor the router's ETH balance on-chain and front-run the `refundETH` call with a zero-cost WETH swap. [6](#0-5) 

---

### Recommendation

Track per-call ETH contributions in **transient storage** (EIP-1153, already used throughout this codebase via `TransientCallbackPool`). At the start of each entry point, record `msg.value` in a dedicated transient slot. In `pay`, consume only up to the recorded per-call ETH budget — not the entire `address(this).balance`. Clear the slot after the call. This ensures ETH from one user's call cannot be consumed by another user's callback. [7](#0-6) 

---

### Proof of Concept

1. **User A** calls `exactInputSingle({tokenIn: WETH, amountIn: 1e18, ...})` sending `1.5e18` ETH as `msg.value`.
2. The pool callback fires; `pay(WETH, userA, pool, 1e18)` is called. `address(this).balance == 1.5e18 >= 1e18`, so the router wraps 1 ETH and sends WETH to the pool. Swap succeeds. **0.5e18 ETH remains in the router.**
3. User A submits a `refundETH()` transaction to recover the 0.5 ETH.
4. **Attacker** observes the pending `refundETH` in the mempool and front-runs it with `exactInputSingle({tokenIn: WETH, amountIn: 0.5e18, ...})` sending **0 ETH**.
5. The pool callback fires; `pay(WETH, attacker, pool, 0.5e18)` is called. `address(this).balance == 0.5e18 >= 0.5e18`, so the router wraps User A's 0.5 ETH and sends WETH to the pool. **Attacker's swap settles at zero cost.**
6. User A's `refundETH()` executes: `address(this).balance == 0`, nothing is returned. **User A loses 0.5 ETH.** [6](#0-5) [8](#0-7)

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-88)
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```

**File:** metric-periphery/contracts/libraries/TransientCallbackPool.sol (L10-13)
```text
  uint256 private constant T_SLOT = 0;
  uint256 private constant T_AMOUNT_IN_SLOT = 1;
  uint256 private constant T_PAYER_SLOT = 2;
  uint256 private constant T_TOKEN_TO_PAY_SLOT = 3;
```
