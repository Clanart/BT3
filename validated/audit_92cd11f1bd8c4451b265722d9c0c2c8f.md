The code is clear enough to make a definitive determination. Let me trace the exact attack path.

**`refundETH()` implementation:** [1](#0-0) 

It sends `address(this).balance` — the entire contract ETH balance — to `msg.sender` with zero access control.

**ETH accumulation path via `pay()`:** [2](#0-1) 

When `token == WETH` and `nativeBalance >= value`, only exactly `value` ETH is wrapped and forwarded. Any excess `msg.value` beyond `value` remains on the router after the transaction ends.

**`receive()` does NOT prevent accumulation:** [3](#0-2) 

It only blocks *direct* ETH sends from non-WETH addresses. All swap entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are `payable` and can receive arbitrary `msg.value`. [4](#0-3) 

**`multicall` is also `payable` and does not refund automatically:** [5](#0-4) 

There is no automatic refund at the end of `multicall`. If a user omits `refundETH()` from their multicall bundle, or calls a payable swap function directly with excess ETH, the surplus persists on the router across transaction boundaries.

---

**Concrete attack path:**

1. User A calls `exactInputSingle` (payable) with `tokenIn = WETH`, `amountIn = 1 ETH`, but sends `msg.value = 2 ETH`.
2. The pool callback fires; `pay()` wraps exactly 1 ETH and sends WETH to the pool. 1 ETH remains on the router.
3. Transaction ends. 1 ETH is now sitting on the router.
4. Attacker calls `refundETH()` in a separate transaction. `address(this).balance = 1 ETH` → transferred to attacker.

This is a direct, permissionless theft of user ETH. No trusted role, no malicious pool, no non-standard token required.

---

### Title
Any caller can drain all ETH held by the router via `refundETH()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`refundETH()` unconditionally transfers the router's entire ETH balance to `msg.sender`. Because all swap entry points are `payable` and `pay()` only consumes exactly the required amount of native ETH, any excess `msg.value` persists on the router after a transaction. A subsequent caller can invoke `refundETH()` to steal that ETH.

### Finding Description
`PeripheryPayments.refundETH()` has no access control and no per-user accounting. It sends `address(this).balance` to whoever calls it. ETH accumulates on the router whenever a user sends more `msg.value` than the swap requires (e.g., `amountIn = 1 ETH` but `msg.value = 2 ETH`), because `pay()` wraps only the exact required amount and leaves the rest. The `receive()` guard only blocks *direct* ETH transfers from non-WETH addresses; it does not prevent accumulation through payable function calls. Once ETH is stranded on the router, any address can call `refundETH()` and claim it all.

### Impact Explanation
Direct loss of user ETH principal. The attacker receives ETH that belongs to a legitimate user. Impact is bounded by the amount of excess ETH sent in the victim's transaction, which can be arbitrarily large.

### Likelihood Explanation
Any user who calls a payable swap function directly (not via multicall, or via multicall without appending `refundETH()`) with excess `msg.value` is vulnerable. This is a common usage pattern, especially for ETH→token swaps where the exact input amount may not be known precisely in advance. A bot monitoring the mempool or pending state can front-run or back-run to call `refundETH()` immediately after the victim's transaction.

### Recommendation
Restrict `refundETH()` to only refund ETH that was sent by `msg.sender` in the current call context, or use transient storage to track per-caller ETH contributions and only refund the recorded amount. Alternatively, automatically refund excess ETH at the end of each swap entry point rather than relying on the caller to include a separate `refundETH()` step.

### Proof of Concept
```solidity
// User A swaps ETH→Token, sends 2 ETH but only 1 ETH is needed
router.exactInputSingle{value: 2 ether}(ExactInputSingleParams({
    tokenIn: WETH, amountIn: 1 ether, ...
}));
// 1 ETH now sits on the router

// Attacker in a separate tx:
router.refundETH(); // receives 1 ETH belonging to User A
assertEq(attacker.balance, 1 ether); // passes
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```
