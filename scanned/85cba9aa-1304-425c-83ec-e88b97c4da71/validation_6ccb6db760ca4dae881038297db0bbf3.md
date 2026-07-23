### Title
Stranded Native ETH on Router Is Silently Consumed by Subsequent WETH Payers, Causing Direct Loss of Prior User's Funds — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments` reads `address(this).balance` (the router's spot native-ETH balance) to satisfy a WETH payment obligation, with no internal accounting of which ETH belongs to which caller. Any native ETH stranded on the router from a prior transaction — e.g., from a `multicall{value: X}` that did not include `refundETH` — is silently consumed by the next user who swaps with WETH as `tokenIn`. The prior user's ETH is permanently lost; the subsequent user pays nothing for their WETH leg.

---

### Finding Description

`PeripheryPayments.pay()` contains the following branch for WETH payments:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol  L73-L84
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← spot balance, no per-user accounting
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
        // payer's WETH is never pulled
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

The function uses the **spot** native-ETH balance of the router contract rather than any per-transaction or per-user internal accounting. This is the direct structural analog of the yDUSD bug: just as `totalAssets` (spot balance) diverged from `totalSupply` (internal accounting) and corrupted share issuance, here `address(this).balance` (spot balance) diverges from "ETH attributable to the current payer" (no internal accounting exists), corrupting payment attribution.

**How ETH becomes stranded:** `multicall` is `payable` and uses `delegatecall`, so `msg.value` is deposited into the router's balance for the duration of the call batch.

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  L39-L44
function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
        results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
}
``` [2](#0-1) 

If a user sends `msg.value = 2 ETH` but only uses 1 ETH in the swap and omits `refundETH`, 1 ETH remains on the router. The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks plain ETH transfers; it does not prevent ETH from arriving via `msg.value` on a `payable` function. [3](#0-2) 

**How the next user steals it:** When any subsequent caller invokes `exactInputSingle` (or `exactOutputSingle`) with `tokenIn = WETH`, the swap callback fires `_justPayCallback`, which calls `pay(WETH, payer=userB, pool, value)`.

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  L192-L199
function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
        _getTokenToPay(),
        _getPayer(),
        msg.sender,
        uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
}
``` [4](#0-3) 

Inside `pay`, `nativeBalance = address(this).balance` picks up User A's stranded ETH. If `nativeBalance >= value`, the router wraps that ETH into WETH and transfers it to the pool — **without ever calling `safeTransferFrom(userB, ...)`**. User B's WETH balance is untouched; User A's ETH is gone.

---

### Impact Explanation

**Direct loss of user principal.** User A's native ETH — sent legitimately as `msg.value` for their own swap — is permanently transferred to the pool on behalf of User B. User A cannot recover it: `refundETH` sends `address(this).balance` to `msg.sender`, but by the time User A calls it, the balance is 0. The loss is proportional to the stranded amount, which can be arbitrarily large (up to `type(uint128).max` per swap, the maximum `amountIn`). [5](#0-4) 

---

### Likelihood Explanation

**Medium.** The trigger is a user who sends `msg.value` in a `multicall` but omits `refundETH` — a realistic mistake given that `refundETH` is optional and not enforced. The exploit requires no privilege: any address can call `exactInputSingle` with `tokenIn = WETH` and zero `msg.value` to drain whatever ETH is currently on the router. MEV bots can monitor the mempool for stranded-ETH multicalls and front-run the refund.

---

### Recommendation

Track the ETH that belongs to the current transaction in transient storage (e.g., store `msg.value` at entry to `multicall`/`exactInputSingle*`/`exactOutputSingle*` and decrement it as it is consumed). In `pay`, only use native ETH up to the tracked per-transaction budget, not the full `address(this).balance`. Alternatively, follow the Uniswap v3 pattern strictly: document that callers **must** append `refundETH` and add a static-analysis lint or a runtime assertion that the router's ETH balance is zero at the end of every non-payable entry point.

---

### Proof of Concept

```
Step 1 — User A strands ETH:
  userA.multicall{value: 2 ETH}([
      exactInputSingle(tokenIn=WETH, amountIn=1 ETH, ...)
      // no refundETH
  ])
  → router.balance == 1 ETH after the call

Step 2 — User B exploits (no msg.value, no WETH approval needed):
  userB.exactInputSingle(tokenIn=WETH, amountIn=1 ETH, ...)
  → metricOmmSwapCallback fires
  → pay(WETH, payer=userB, pool, 1 ETH)
      nativeBalance = 1 ETH  ≥  value = 1 ETH
      WETH.deposit{value: 1 ETH}()   // uses userA's ETH
      WETH.transfer(pool, 1 ETH)     // pool receives WETH
      // safeTransferFrom(userB, ...) is NEVER called
  → userB completes swap for free
  → userA's 1 ETH is permanently lost
```

The corrupted value is exactly `min(address(this).balance, amountIn)` ETH stolen from the prior depositor per exploit transaction.

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
