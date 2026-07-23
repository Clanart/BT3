Based on my investigation of the codebase, I found a valid analog in the `PeripheryPayments.sol` payment path.

---

### Title
Stranded ETH on Router Consumed by Any WETH Swap, Enabling Theft of User Funds - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

### Summary

The `pay()` function in `PeripheryPayments.sol` uses the router's native ETH balance (`address(this).balance`) to settle WETH swap payments without verifying that the ETH belongs to the current payer. Any ETH stranded on the router from a prior transaction is silently consumed by the next WETH swap, transferring value from the original depositor to the attacker.

### Finding Description

In `PeripheryPayments.sol`, the `pay()` function handles WETH payments as follows: [1](#0-0) 

When `token == WETH`, the function checks `address(this).balance` — the router's total native ETH balance — and uses it to wrap and forward ETH to the pool, regardless of which user's transaction deposited that ETH. The `payer` argument is only consulted as a fallback when the router holds zero ETH.

The router accumulates ETH whenever a user sends `msg.value` for a WETH swap (e.g., via `exactInputSingle` or `exactInput`) but the swap does not consume the full amount — for example, due to a price limit being hit, a partial fill, or simply sending excess ETH. The standard remedy is to call `refundETH()` in the same multicall: [2](#0-1) 

However, `refundETH()` is a separate, optional call. If a user omits it — or if ETH arrives on the router through any other path — that ETH persists across transactions and is available to any subsequent caller.

The `multicall` entry point uses `delegatecall`, so `msg.value` is shared across all sub-calls within a single multicall bundle: [3](#0-2) 

This means a user who sends ETH in a multicall but omits `refundETH()` leaves ETH on the router permanently until another party sweeps it.

The swap callback that triggers `pay()` is: [4](#0-3) 

The callback validates only that `msg.sender` is the expected pool (via `_requireExpectedCallbackCaller`). It does not validate that the ETH being used to settle the payment was deposited by the current payer. Any attacker who initiates a WETH swap through the router while the router holds ETH will have their payment settled from that ETH balance instead of from their own funds.

### Impact Explanation

Direct loss of user ETH principal. A victim who sends ETH to the router (for a WETH swap) and does not include `refundETH()` in the same multicall loses their unspent ETH to the next party who calls any WETH swap through the router. The attacker receives the full swap output without paying the corresponding input cost. This satisfies the Critical/High direct loss of user principal threshold.

### Likelihood Explanation

Medium. The precondition — ETH stranded on the router — is a realistic and common user error. Uniswap v3 periphery has documented this exact pattern as a known footgun. Users who call `exactInputSingle` with `msg.value` for WETH swaps and omit `refundETH()` are the victim class. An attacker can monitor the mempool or the router's ETH balance on-chain and immediately exploit any nonzero balance with a single `exactInputSingle` call sending 0 ETH.

### Recommendation

In `pay()`, when `token == WETH` and `payer != address(this)`, do not use `address(this).balance` to settle the payment. Instead, always pull WETH directly from the payer via `safeTransferFrom`. Reserve the native-ETH wrapping path exclusively for the case where `payer == address(this)` (i.e., the router is paying from its own balance as an intermediate hop). Alternatively, track per-user ETH deposits in transient storage and only allow each user's swap to consume their own deposited ETH.

### Proof of Concept

1. Victim calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = 100`, sends `msg.value = 100 ETH`. The swap partially fills (e.g., price limit hit), consuming only 80 ETH. The victim does not include `refundETH()`. 20 ETH remains on the router.
2. Attacker calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = 20`, sends `msg.value = 0`.
3. The pool calls `metricOmmSwapCallback` on the router. The router calls `pay(WETH, attacker, pool, 20)`.
4. Inside `pay()`: `address(this).balance == 20 >= 20`, so the router wraps the victim's 20 ETH and transfers WETH to the pool.
5. The attacker receives the swap output (token0 or token1) without spending any ETH or WETH.
6. The victim's 20 ETH is permanently lost.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L55-63)
```text
  }

  /// @inheritdoc IPeripheryPayments
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L46-62)
```text
  function metricOmmSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external override {
    if (amount0Delta <= 0 && amount1Delta <= 0) revert InvalidSwapDeltas();

    _requireExpectedCallbackCaller(msg.sender);

    uint8 callbackMode = _getCallbackMode();

    if (callbackMode == CALLBACK_MODE_JUST_PAY) {
      _justPayCallback(amount0Delta, amount1Delta);
      return;
    }
    if (callbackMode == CALLBACK_MODE_EXACT_OUTPUT_ITERATE) {
      _exactOutputIterateCallback(amount0Delta, amount1Delta, data);
      return;
    }
    revert InvalidCallbackMode(callbackMode);
  }
```
