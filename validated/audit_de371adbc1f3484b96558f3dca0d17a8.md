The code confirms all claims. Let me verify the `pay()` function behavior more carefully before concluding.

Audit Report

## Title
Unguarded `refundETH()` allows any caller to drain stranded ETH left on the router between transactions — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` has no access control and unconditionally transfers the router's entire ETH balance to `msg.sender`. When a user sends excess `msg.value` in a `multicall` but omits the `refundETH` call, the surplus ETH is left on the router after the transaction completes. Any third party — including MEV bots — can immediately claim it in a subsequent transaction by calling `refundETH()`.

## Finding Description

`refundETH()` is implemented with no caller restriction:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [1](#0-0) 

ETH accumulates on the router via `msg.value` attached to payable entry points (`multicall`, `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). Inside the swap callback, `pay()` wraps only the exact amount needed:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    }
``` [2](#0-1) 

When `nativeBalance > value`, the surplus `nativeBalance - value` is silently left on the router. The `receive()` guard only blocks direct ETH transfers from non-WETH senders; it does not prevent ETH accumulation via `msg.value` on payable calls. [3](#0-2) 

`multicall` executes sub-calls sequentially and returns without any automatic ETH refund: [4](#0-3) 

`refundETH` is opt-in — users must explicitly append it to their multicall array. Any user who omits it leaves the surplus exposed for the entire block and beyond, claimable by any address.

## Impact Explanation

Direct loss of user ETH principal. The attacker receives ETH belonging to the victim with no preconditions other than timing. The amount is bounded only by how much excess ETH the victim sent, which can be arbitrarily large. This satisfies the "Critical/High/Medium direct loss of user principal" impact gate.

## Likelihood Explanation

- `refundETH` is opt-in; omitting it is a realistic mistake for users interacting directly with the contract or through integrations that do not enforce the pattern. The native test suite itself documents the correct pattern (`test_multicall_ethInput_exactInputSingle_refundsUnusedEth`) but does not enforce it at the contract level.
- The attack requires no special privileges, no malicious pool, and no non-standard token behavior — just a call to a public function.
- MEV bots routinely monitor for stranded ETH on router contracts and can execute the steal atomically in the same block.

## Recommendation

Two complementary mitigations:

1. **Track the depositor in transient storage** at the start of `multicall` and restrict `refundETH` to that address within the same call context.
2. **Auto-refund at the end of `multicall`**: after executing all sub-calls, if `address(this).balance > 0`, transfer the remainder back to `msg.sender` unconditionally, removing the need for users to remember to append the call.

## Proof of Concept

The existing test `test_refundETH_sendsBalanceToCaller` already demonstrates that any arbitrary `swapper` address (not the original depositor) can drain the router's ETH balance by calling `refundETH()`: [5](#0-4) 

Exploit steps:
```
1. User calls:
   router.multicall{value: 1 ether}([
       abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams{
           tokenIn: WETH,
           amountIn: 0.5 ether,   // only 0.5 ETH is wrapped and sent to pool
           ...
       }))
       // refundETH omitted
   ]);

2. After the multicall:
   address(router).balance == 0.5 ether   // surplus sits on router

3. Attacker calls (separate tx, same or next block):
   router.refundETH();

4. Result:
   attacker.balance += 0.5 ether   // stolen from victim
   address(router).balance == 0
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.payments.t.sol (L74-85)
```text
  function test_refundETH_sendsBalanceToCaller() public {
    uint256 amount = 2 ether;
    vm.deal(address(router), amount);

    uint256 swapperBefore = swapper.balance;

    vm.prank(swapper);
    router.refundETH();

    assertEq(swapper.balance - swapperBefore, amount, "swapper refunded");
    assertEq(address(router).balance, 0, "router eth cleared");
  }
```
