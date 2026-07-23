### Title
Unguarded `refundETH()` allows any caller to steal excess ETH left on the router between transactions — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` has no access control and sends the router's entire ETH balance to `msg.sender`. Because `pay()` wraps only the exact amount of native ETH needed for a WETH swap and leaves any excess on the contract, a user who sends more ETH than required in a `multicall` but omits the `refundETH` call leaves a stranded balance that any third party can immediately claim in a subsequent transaction.

---

### Finding Description

`refundETH()` is implemented as:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [1](#0-0) 

There is no check that `msg.sender` is the address that originally deposited the ETH. The function unconditionally drains the full balance to whoever calls it.

ETH accumulates on the router through `msg.value` attached to payable entry points (`multicall`, `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). Inside the swap callback, `pay()` wraps only the exact amount needed:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    }
``` [2](#0-1) 

If `nativeBalance > value`, the surplus ETH is silently left on the router. The `receive()` guard (which rejects direct ETH transfers from non-WETH senders) does not prevent this accumulation because the ETH arrives via `msg.value` on a payable call, not through `receive()`. [3](#0-2) 

The `multicall` implementation does not automatically append a `refundETH` call, so any user who omits it leaves the surplus exposed for the entire block (and beyond). [4](#0-3) 

---

### Impact Explanation

Direct loss of user ETH principal. The attacker receives ETH that belongs to the victim with no preconditions other than timing. The amount is bounded only by how much excess ETH the victim sent, which can be arbitrarily large (e.g., a user who sends 1 ETH for a 1-wei swap loses nearly 1 ETH).

---

### Likelihood Explanation

- `refundETH` is an opt-in call that users must explicitly include in their multicall array. Omitting it is a realistic mistake, especially for users interacting directly with the contract or through integrations that do not enforce the pattern.
- The attack requires no special privileges, no malicious pool, and no non-standard token behavior — just a call to a public function.
- MEV bots routinely monitor for stranded ETH on router contracts and can execute the steal atomically in the same block.

---

### Recommendation

Two complementary mitigations:

1. **Track the depositor in transient storage** at the start of `multicall` and restrict `refundETH` to that address within the same call context.
2. **Auto-refund at the end of `multicall`**: after executing all sub-calls, if `address(this).balance > 0`, transfer the remainder back to `msg.sender` unconditionally, removing the need for users to remember to append the call.

---

### Proof of Concept

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

The test `test_refundETH_sendsBalanceToCaller` in the existing test suite already demonstrates that any arbitrary `swapper` address (not the original depositor) can drain the router's ETH balance by calling `refundETH()`. [5](#0-4)

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
