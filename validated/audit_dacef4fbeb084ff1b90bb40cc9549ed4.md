The attack path is real and provable from the code. Here is the full analysis:

---

### Title
`refundETH()` Has No Access Control — Any Caller Steals Excess ETH Left on Router After a Partial-ETH Swap - (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`refundETH()` unconditionally transfers the router's entire ETH balance to `msg.sender` with no ownership check. Because `pay()` deposits only the exact swap amount as WETH and leaves any excess native ETH on the router, a user who sends more ETH than needed without including `refundETH` in the same multicall permanently exposes that excess to any subsequent caller.

### Finding Description

`refundETH()` is declared `external payable` with zero access control:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [1](#0-0) 

It sends the **entire** router ETH balance to whoever calls it, with no check that the caller is the one who deposited the ETH.

ETH becomes stranded on the router through the `pay()` function. When `token == WETH` and `nativeBalance >= value`, it deposits exactly `value` ETH as WETH and transfers it to the pool — the remainder stays on the router:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    }
``` [2](#0-1) 

The `receive()` guard prevents arbitrary direct ETH deposits, but it does **not** prevent ETH from being stranded via `payable` swap entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) when the user sends more ETH than the swap consumes. [3](#0-2) 

The intended safe pattern — including `refundETH` in the same `multicall` — is documented and tested, but it is never enforced by the contract: [4](#0-3) 

If a user calls `exactInputSingle{value: X}(...)` directly (not via multicall), or forgets to append `refundETH` to their multicall, the excess ETH persists on the router across transaction boundaries and is claimable by anyone.

### Impact Explanation

Direct loss of user principal. The victim loses the excess ETH they sent; the attacker gains it with a single zero-cost call. The stolen amount equals `msg.value - amountIn` for every such transaction, which can be arbitrarily large.

### Likelihood Explanation

The pattern of sending excess ETH and relying on `refundETH` is the documented and expected usage. Users calling swap functions directly (without multicall) or omitting `refundETH` from a multicall is a realistic mistake, especially for integrators or less experienced users. An attacker can monitor the mempool or simply poll the router's ETH balance to detect and front-run or back-run the victim's transaction.

### Recommendation

Track per-caller ETH deposits in transient storage at the start of each `payable` entry point and restrict `refundETH` to return only the calling address's recorded deposit. Alternatively, automatically refund any remaining ETH balance to `msg.sender` at the end of each swap function, eliminating the need for a separate `refundETH` call.

### Proof of Concept

1. User calls `router.exactInputSingle{value: 1 ether}(params)` where `params.tokenIn = WETH`, `params.amountIn = 0.5 ether`, without including `refundETH` in a multicall.
2. Inside the swap callback, `pay()` is invoked with `value = 0.5 ether`. Since `address(this).balance (1 ETH) >= value (0.5 ETH)`, it deposits exactly 0.5 ETH as WETH and transfers it to the pool. The remaining 0.5 ETH stays on the router.
3. The user's transaction completes. `address(router).balance == 0.5 ether`.
4. Attacker calls `router.refundETH()` in the next transaction.
5. `balance = 0.5 ether`; `_transferETH(attacker, 0.5 ether)` executes.
6. Attacker receives 0.5 ETH. Victim's excess ETH is gone.

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-77)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
