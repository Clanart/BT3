The vulnerability is real. Let me trace the exact execution path before writing the finding.

The `pay` function in `PeripheryPayments.sol` reads `address(this).balance` with no attribution to the current caller: [1](#0-0) 

When `token == WETH` and `payer != address(this)`, the function checks the router's entire native ETH balance. If `0 < nativeBalance < value`, it deposits and transfers the stranded ETH first, then pulls only the remainder from `payer`. There is no check that the native ETH was sent by the current `payer` or in the current transaction.

The `multicall` function is `payable` and uses `delegatecall`, so `msg.value` is shared across all steps: [2](#0-1) 

The `exactInputSingle` and `exactOutputSingle` entry points set `payer = msg.sender` and call into the pool, which triggers `_justPayCallback` → `pay(tokenIn, msg.sender, pool, amount)`: [3](#0-2) [4](#0-3) 

---

### Title
Stranded native ETH on the router is silently consumed to subsidize a later user's WETH input payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay()` uses the router's entire native ETH balance (`address(this).balance`) to partially fund a WETH input payment without verifying that the ETH was sent by the current payer in the current transaction. Any ETH left on the router from a prior user's transaction is silently consumed to reduce the WETH pulled from the current payer.

### Finding Description
When `pay(WETH, payer, pool, value)` is called and `0 < address(this).balance < value`, the function executes:

```solidity
IWETH9(WETH).deposit{value: nativeBalance}();
IERC20(WETH).safeTransfer(recipient, nativeBalance);
IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
``` [5](#0-4) 

`nativeBalance` is `address(this).balance` — the router's total ETH balance — with no attribution to the current `payer` or the current transaction. ETH stranded from any prior call (e.g., a user who called `exactInputSingle{value: X}(...)` with `X > amountIn` and omitted `refundETH()`) is indistinguishable from ETH the current payer intentionally sent.

**Stranding path:** A user calls `exactInputSingle{value: X}(...)` with WETH as `tokenIn` and `amountIn < X`. The `nativeBalance >= value` branch fires, deposits only `amountIn`, and the remaining `X - amountIn` ETH stays on the router. Without a `refundETH()` call, it is permanently stranded until the next WETH payment. [6](#0-5) 

**Exploitation path:** An attacker (or any subsequent user) calls `exactInputSingle` with WETH as `tokenIn`. The `pay` function sees `nativeBalance = stranded ETH > 0`, enters the partial-native branch, deposits the victim's ETH as WETH, transfers it to the pool, and pulls only `value - nativeBalance` from the attacker's wallet. The attacker pays less WETH than the swap requires; the victim's ETH is consumed.

### Impact Explanation
Direct loss of user principal. The victim loses the ETH they sent to the router that was not consumed by their own swap. The attacker receives a discount on their WETH input equal to the stranded amount. This satisfies the contest's "direct loss of user principal" threshold at Medium severity.

### Likelihood Explanation
Any user who calls a payable swap function with excess ETH and omits `refundETH()` creates the precondition. This is a realistic mistake — the test suite itself demonstrates the pattern of sending `msg.value = amountIn` exactly, but nothing prevents a user from sending more. An attacker can monitor the mempool or the router's ETH balance and immediately follow with a WETH swap to drain it. [7](#0-6) 

### Recommendation
Track how much ETH the current transaction deposited (e.g., store `msg.value` at the top of each payable entry point in transient storage) and cap the native ETH consumed in `pay` to that amount. Alternatively, only consume native ETH when `payer == msg.sender` and `msg.value > 0`, and limit consumption to `min(nativeBalance, msg.value_remaining)`. The simplest safe fix is to pass the caller-supplied ETH amount explicitly through the call stack rather than reading the ambient `address(this).balance`.

### Proof of Concept
```solidity
// Step 1: victim strands ETH on the router
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    tokenIn: address(weth),
    amountIn: 0.4 ether,   // only 0.4 ETH consumed; 0.6 ETH stranded
    // ... no refundETH() call
}));
// address(router).balance == 0.6 ether

// Step 2: attacker exploits stranded ETH
uint256 wethBefore = weth.balanceOf(attacker);
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: address(weth),
    amountIn: 1 ether,     // pay() sees nativeBalance=0.6 > 0, < 1 ETH
    // ...
}));
// attacker's WETH spent == 0.4 ether (not 1 ether)
// victim's 0.6 ETH is gone
assert(wethBefore - weth.balanceOf(attacker) == 0.4 ether);
assert(address(router).balance == 0);
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```
