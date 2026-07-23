### Title
Unguarded native ETH balance in `pay()` allows any caller to drain ETH left by prior overpaying users — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments` uses the router's **entire** native ETH balance to fund WETH payments without verifying that the ETH was deposited by the current caller. Because every swap entry-point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) is `payable`, ETH from one user's overpayment can sit in the router between transactions. A subsequent caller who initiates any WETH-input swap with 0 ETH will have the router's accumulated ETH wrapped and forwarded to the pool on their behalf, stealing the prior user's funds.

---

### Finding Description

`PeripheryPayments.pay()` handles the WETH leg of a swap as follows: [1](#0-0) 

```solidity
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
        uint256 nativeBalance = address(this).balance;
        if (nativeBalance >= value) {
            IWETH9(WETH).deposit{value: value}();
            IERC20(WETH).safeTransfer(recipient, value);   // ← uses router's ETH, not payer's
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

When `nativeBalance >= value`, the function wraps and forwards the router's ETH **without pulling anything from `payer`**. There is no check that the ETH in the router was deposited by the current caller.

ETH accumulates in the router because:

1. All swap entry-points are `payable` — users routinely send a round-number ETH amount and rely on `refundETH()` to recover the excess. [2](#0-1) 

2. `refundETH()` must be called **explicitly** (e.g., as a trailing multicall leg); if omitted, the excess persists across transactions. [3](#0-2) 

3. The `receive()` guard only blocks direct ETH pushes; it does not prevent ETH from entering via `payable` function calls. [4](#0-3) 

The callback path that reaches `pay()` stores `payer = msg.sender` of the **attacker's** call, but `pay()` never uses `payer`'s funds when the router already holds enough ETH: [5](#0-4) 

---

### Impact Explanation

**Critical / High — direct loss of user ETH principal.**

Any ETH left in the router (from any prior user's overpayment) is fully consumable by the next WETH-input swap. The attacker receives the full swap output (e.g., USDC) while contributing 0 ETH. The victim's ETH is permanently transferred to the pool on the attacker's behalf with no recourse.

---

### Likelihood Explanation

**Medium–High.**

- Overpaying ETH in DeFi is extremely common (users send a round number and expect a refund).
- Forgetting to append `refundETH()` to a multicall is a realistic user error.
- The attack requires no special privileges, no malicious setup, and no flash loan — a single `exactInputSingle` call with 0 ETH suffices.
- The window is one block (or longer if the victim's refund is simply omitted), which is easily exploitable by a bot watching the mempool.

---

### Recommendation

**Short term**: Track each caller's deposited ETH in transient storage at the entry-point and cap `pay()`'s use of native balance to that per-caller deposit. Clear the slot after use.

**Long term**: At the end of every swap function (or as a mandatory multicall tail), assert `address(this).balance == 0` and revert otherwise, forcing users to always include `refundETH()`. Alternatively, auto-refund excess ETH at the end of each top-level swap call rather than relying on the caller to do so.

---

### Proof of Concept

```
// Step 1 – Victim overpays
victim calls exactInputSingle{value: 2 ether}(
    tokenIn  = WETH,
    amountIn = 1 ether,
    ...
)
// Pool receives 1 WETH; 1 ETH remains in the router.
// Victim does NOT call refundETH().

// Step 2 – Attacker steals the stranded ETH
attacker calls exactInputSingle{value: 0}(
    tokenIn  = WETH,
    amountIn = 1 ether,   // matches the stranded balance
    recipient = attacker,
    ...
)
// pay() sees address(this).balance == 1 ether >= value == 1 ether
// → wraps 1 ETH, transfers 1 WETH to pool
// → pool sends output token (e.g., USDC) to attacker
// Attacker receives USDC; victim's 1 ETH is gone.
```

The attacker needs zero ETH and zero WETH approval. The only precondition is that the router holds a non-zero ETH balance, which is a routine post-condition of any overpaying WETH swap.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
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
