Looking at the two key pieces of code:

The code confirms the vulnerability path is real. Here is the complete analysis:

---

### Title
Unguarded `refundETH()` allows any caller to drain excess ETH left on the router — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`refundETH()` has no access control and unconditionally sends the router's entire ETH balance to `msg.sender`. Because `pay()` only wraps the exact swap amount into WETH and leaves any overpayment as raw ETH on the contract, a user who sends excess ETH without atomically calling `refundETH()` in the same multicall loses that ETH to the first external caller.

### Finding Description

`refundETH()` is `external` with no ownership or caller check: [1](#0-0) 

`pay()` consumes only `value` wei of the router's native balance when `token == WETH`, leaving any surplus untouched: [2](#0-1) 

`receive()` blocks plain ETH transfers from non-WETH addresses, but it is **not** invoked when ETH arrives as `msg.value` on a named `payable` function. `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, and `multicall` are all `payable`, so a user legitimately sends ETH with those calls: [3](#0-2) [4](#0-3) 

### Impact Explanation

Direct ETH loss for any user who:
- Sends more ETH than `amountIn` (common practice to guarantee a swap succeeds), **and**
- Does not atomically call `refundETH()` in the same multicall (e.g., calls `exactInputSingle` directly, or uses a frontend that omits the refund step).

An attacker watching the mempool or the router's balance calls `refundETH()` immediately after the victim's swap transaction is mined and receives the full surplus. No privileged role, malicious pool, or non-standard token is required.

### Likelihood Explanation

- Users routinely overpay ETH on WETH-input swaps to avoid reverts from price movement.
- Calling swap functions directly (without multicall) is a supported and documented path.
- Frontends or integrators that omit `refundETH()` from the multicall bundle expose every such user.
- The attack is zero-cost and permissionless.

### Recommendation

Track the ETH depositor in transient storage at the start of each top-level call and restrict `refundETH()` to that address, or automatically sweep excess ETH back to `msg.sender` at the end of each swap entry point rather than relying on a separate, unguarded call.

### Proof of Concept

1. User calls `exactInputSingle{value: 1 ether}(params)` where `params.tokenIn == WETH` and `params.amountIn == 0.5 ether`.
2. Inside the swap callback, `pay()` wraps exactly `0.5 ether` into WETH and transfers it to the pool; `0.5 ether` remains on the router.
3. The swap completes; the user does **not** call `refundETH()`.
4. Attacker calls `refundETH()` in the next transaction.
5. `_transferETH(msg.sender, 0.5 ether)` executes; attacker receives `0.5 ether`. User's surplus is gone.

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
