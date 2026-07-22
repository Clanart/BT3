### Title
Excess native ETH sent to payable swap functions is permanently stranded and can be stolen via `refundETH()` - (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
The `pay()` helper in `PeripheryPayments` wraps only the exact amount of native ETH the pool requests, leaving any excess ETH the user attached to a `payable` swap call sitting in the router. Because `refundETH()` is a public function that forwards the entire contract ETH balance to `msg.sender`, a front-runner can steal that excess ETH before the original caller reclaims it.

### Finding Description

Every swap entry-point on `MetricOmmSimpleRouter` is `payable`: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

When `tokenIn == WETH`, the swap callback eventually calls `pay()`. Inside `pay()`, the branch for native ETH is:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();   // wraps only `value`
    IERC20(WETH).safeTransfer(recipient, value);
} else if (nativeBalance > 0) { ...
``` [5](#0-4) 

The condition `nativeBalance >= value` accepts any surplus. Only exactly `value` is wrapped and forwarded; the remainder (`nativeBalance - value`) stays in the router.

`refundETH()` is unrestricted — it sends the **entire** contract ETH balance to whoever calls it:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [6](#0-5) 

The `receive()` guard only blocks plain ETH transfers from non-WETH addresses; it does **not** block ETH attached to a function call: [7](#0-6) 

### Impact Explanation

A user who calls `exactInputSingle{value: X}(params)` with `tokenIn == WETH` and `X > amountIn` loses `X - amountIn` ETH. The surplus is not returned automatically; it sits in the router until any third party calls `refundETH()` and claims it. This is a direct, permanent loss of user principal with no recovery path once front-run.

**Impact: High** — user ETH is stolen, not merely locked.

### Likelihood Explanation

**Likelihood: Low** — requires the user to attach more ETH than the swap needs. This can happen via a buggy integration, a slippage miscalculation, or a user manually over-funding a WETH-input swap. The multicall pattern (`exactInputSingle` + `refundETH`) is the intended safe path, but a direct single call with excess ETH is a realistic mistake.

### Recommendation

After the swap settles, refund any remaining native balance to `msg.sender` inside each `payable` swap function, or enforce that the attached ETH equals exactly the amount consumed:

```solidity
// At the end of exactInputSingle / exactInput / exactOutputSingle / exactOutput:
if (address(this).balance > 0) {
    _transferETH(msg.sender, address(this).balance);
}
```

Alternatively, restrict `pay()` to reject surplus ETH analogously to the external report's recommendation:

```diff
- if (nativeBalance >= value) {
+ if (nativeBalance == value) {
```

### Proof of Concept

1. Pool has a WETH/USDC pair. `amountIn` for a given swap is `1 ETH`.
2. User calls `exactInputSingle{value: 2 ether}(params)` with `tokenIn = WETH`.
3. Pool triggers `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, user, pool, 1e18)`.
4. `pay()` sees `nativeBalance = 2e18 >= 1e18 = value`, wraps `1e18`, transfers to pool. `1e18` ETH remains in router.
5. Attacker observes the transaction in the mempool (or after inclusion), calls `refundETH()`.
6. `refundETH()` sends the full `1e18` ETH balance to the attacker.
7. User receives correct swap output but loses `1 ETH` of excess input. [8](#0-7)

### Citations

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
