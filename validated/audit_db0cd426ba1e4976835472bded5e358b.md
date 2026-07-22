### Title
ETH Sent With Non-WETH Swap or Liquidity Calls Is Silently Consumed and Stealable by Any Caller via `refundETH()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

Every user-facing entry point in `MetricOmmSimpleRouter` (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and `MetricOmmPoolLiquidityAdder` (`addLiquidityExactShares`, `addLiquidityWeighted`) is declared `payable`, so the EVM silently accepts any `msg.value`. When the swap or liquidity token is **not** WETH, the internal `pay()` function falls into its `else` branch and calls `safeTransferFrom` for the ERC20 while leaving `address(this).balance` completely untouched. The ETH is then stranded in the router/adder. Because `refundETH()` unconditionally sends the **entire** ETH balance to `msg.sender` (not to the original depositor), any third party can immediately steal the stranded ETH by calling `refundETH()` after the victim's transaction.

---

### Finding Description

`PeripheryPayments.pay()` has three branches:

```
payer == address(this)  →  safeTransfer (mid-path ERC20)
token == WETH           →  use native balance first, then pull WETH from payer
else                    →  safeTransferFrom(payer, recipient, value)   ← ignores address(this).balance entirely
``` [1](#0-0) 

All four router swap functions and both liquidity-adder entry points are `payable`: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

The `receive()` guard only blocks plain ETH transfers (no calldata); it does **not** prevent ETH from being attached to a `payable` function call: [7](#0-6) 

`refundETH()` sends the full balance to `msg.sender`, not to the original depositor: [8](#0-7) 

---

### Impact Explanation

A user who calls `exactInputSingle{value: V}(…)` with `tokenIn = USDC` (or any non-WETH token):

1. Pays the correct USDC amount via `safeTransferFrom` — the swap succeeds normally.
2. Also loses `V` ETH, which is silently accepted by the `payable` function and left in the router.
3. Any third party can immediately call `refundETH()` and receive the full `V` ETH, because `refundETH` sends to `msg.sender` with no access control.

The same applies to `addLiquidityExactShares{value: V}(…)` when neither pool token is WETH. The user suffers a direct, irrecoverable loss of principal equal to `V`. The attacker's cost is only the gas for one `refundETH()` call.

---

### Likelihood Explanation

- Frontends commonly pre-populate `msg.value` for WETH-input swaps; a UI bug or copy-paste error can attach ETH to a non-WETH call.
- Users familiar with native-ETH DEX patterns (e.g., Uniswap v2 `swapExactETHForTokens`) may manually attach ETH expecting the router to handle it.
- The `multicall` pattern encourages batching; a user who includes a WETH swap and a non-WETH swap in the same `multicall{value}` call will have residual ETH after the WETH leg, which the non-WETH leg ignores — and that residual is stealable if the user forgets to append `refundETH`.
- MEV bots routinely monitor for stranded ETH in well-known router contracts and will steal it within the same block. [9](#0-8) 

---

### Recommendation

Two complementary fixes:

1. **Reject unexpected ETH**: In each non-WETH entry point, add `if (msg.value > 0 && params.tokenIn != WETH) revert UnexpectedETH();`. Alternatively, add a modifier `noValueUnlessWeth(address tokenIn)`.

2. **Scope `refundETH` to the original caller**: Store the original `msg.sender` in transient storage at the start of each top-level call and restrict `refundETH` to that address, or remove the `payable` modifier from `refundETH` itself so it cannot be called with ETH by a third party in the same multicall.

---

### Proof of Concept

```solidity
// Alice swaps USDC → token1, accidentally attaches 1 ETH
vm.prank(alice);
router.exactInputSingle{value: 1 ether}(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(usdcPool),
        tokenIn:         address(usdc),   // NOT WETH
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        1_000e6,
        amountOutMinimum: 0,
        recipient:       alice,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// Swap succeeds; Alice paid 1 000 USDC AND lost 1 ETH silently.
assertEq(address(router).balance, 1 ether); // ETH stranded

// Bob steals it
vm.prank(bob);
router.refundETH();
assertEq(bob.balance, 1 ether);   // Bob received Alice's ETH
assertEq(address(router).balance, 0);
```

The `pay` call inside `_justPayCallback` takes the `else` branch (line 86) because `token == USDC ≠ WETH`, pulls USDC from Alice, and never touches `address(this).balance`. The 1 ETH remains in the router until Bob calls `refundETH()`. [10](#0-9) [11](#0-10)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-64)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
```
