Audit Report

## Title
ETH Sent With Non-WETH Swap or Liquidity Calls Is Silently Consumed and Stealable by Any Caller via `refundETH()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
All four swap entry points in `MetricOmmSimpleRouter` and both liquidity entry points in `MetricOmmPoolLiquidityAdder` are declared `payable`, so the EVM silently accepts any `msg.value`. When the input token is not WETH, `pay()` takes its `else` branch and calls `safeTransferFrom` for the ERC20 while leaving `address(this).balance` completely untouched. Because `refundETH()` unconditionally forwards the entire ETH balance to `msg.sender` with no access control, any third party can immediately steal the stranded ETH.

## Finding Description
`PeripheryPayments.pay()` has three branches: [1](#0-0) 

When `token != WETH` and `payer != address(this)`, execution falls to line 86 (`safeTransferFrom`), which pulls the ERC20 from the payer and returns — `address(this).balance` is never touched. Any ETH attached to the call remains stranded in the contract.

All four router swap functions are `payable`: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

Both liquidity entry points are also `payable`: [6](#0-5) [7](#0-6) 

The `receive()` guard only blocks plain ETH transfers (no calldata); it does not prevent ETH from being attached to a `payable` function call: [8](#0-7) 

`refundETH()` sends the full balance to `msg.sender` with no restriction on who may call it: [9](#0-8) 

Exploit path: Alice calls `exactInputSingle{value: V}(tokenIn=USDC, ...)`. The pool callback fires `_justPayCallback`, which calls `pay(USDC, alice, pool, amount)`. Because `USDC != WETH`, the `else` branch executes `safeTransferFrom(alice, pool, amount)` and returns. `V` ETH remains in the router. Bob calls `refundETH()` and receives `V` ETH.

## Impact Explanation
Direct, irrecoverable loss of user ETH principal. The stranded ETH is immediately claimable by any unprivileged caller via `refundETH()`. This matches the "Critical/High direct loss of user principal" allowed impact. The victim loses the full attached `msg.value`; the attacker's cost is a single gas call.

## Likelihood Explanation
- Frontends commonly pre-populate `msg.value` for WETH-input swaps; a UI bug or copy-paste error can attach ETH to a non-WETH call.
- Users familiar with native-ETH DEX patterns (e.g., Uniswap v2 `swapExactETHForTokens`) may manually attach ETH expecting the router to handle it.
- The `multicall` pattern encourages batching; a user who includes a WETH swap and a non-WETH swap in the same `multicall{value}` call will have residual ETH after the WETH leg that the non-WETH leg ignores — stealable if the user omits `refundETH`.
- MEV bots routinely monitor for stranded ETH in well-known router contracts and will steal it within the same block.

## Recommendation
Two complementary fixes:

1. **Reject unexpected ETH at entry points**: In each non-WETH entry point, add a check such as `if (msg.value > 0 && params.tokenIn != WETH) revert UnexpectedETH();`. Alternatively, add a modifier `noValueUnlessWeth(address tokenIn)` applied to all swap and liquidity functions.

2. **Scope `refundETH` to the original caller**: Store the original `msg.sender` in transient storage at the start of each top-level call and restrict `refundETH` to that address, or remove the `payable` modifier from non-WETH entry points entirely so ETH cannot be attached in the first place.

## Proof of Concept
```solidity
// Alice swaps USDC → token1, accidentally attaches 1 ETH
vm.prank(alice);
router.exactInputSingle{value: 1 ether}(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(usdcPool),
        tokenIn:          address(usdc),   // NOT WETH
        tokenOut:         address(token1),
        zeroForOne:       true,
        amountIn:         1_000e6,
        amountOutMinimum: 0,
        recipient:        alice,
        deadline:         block.timestamp + 1,
        priceLimitX64:    0,
        extensionData:    ""
    })
);
// Swap succeeds; Alice paid 1 000 USDC AND lost 1 ETH silently.
assertEq(address(router).balance, 1 ether); // ETH stranded

// Bob steals it
vm.prank(bob);
router.refundETH();
assertEq(bob.balance, 1 ether);            // Bob received Alice's ETH
assertEq(address(router).balance, 0);
```

The `pay` call inside `_justPayCallback` takes the `else` branch at line 86 of `PeripheryPayments.sol` because `token == USDC != WETH`, pulls USDC from Alice via `safeTransferFrom`, and never touches `address(this).balance`. The 1 ETH remains in the router until Bob calls `refundETH()`. [10](#0-9) [11](#0-10)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L100-100)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
```
