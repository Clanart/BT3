The `refundETH()` function is unguarded and sends the entire router ETH balance to `msg.sender`. The question is whether ETH can realistically be stranded on the router between transactions for an attacker to exploit.

**ETH entry path analysis:**

The `receive()` guard at line 32-34 blocks all direct ETH transfers except from WETH: [1](#0-0) 

ETH can only enter the router via `msg.value` in payable calls (e.g., `multicall{value: X}`). The `pay()` function at lines 74-77 wraps exactly `value` ETH into WETH when `nativeBalance >= value`, leaving `nativeBalance - value` as raw ETH on the router: [2](#0-1) 

**The stranding scenario:**

A user calls `multicall{value: 2 ether}([exactInputSingle(amountIn=1000)])` without appending `refundETH()`. The swap wraps 1000 wei; `2 ether - 1000 wei` remains as raw ETH on the router after the transaction. In a subsequent transaction, any attacker calls `refundETH()` and receives the full balance: [3](#0-2) 

The test suite explicitly confirms this design pattern is expected and that `refundETH()` is the only mechanism to recover excess ETH: [4](#0-3) 

**Assessment:**

The vulnerability is real but requires user error — the user must send excess ETH in a multicall without including `refundETH()` as the final call. The `receive()` guard limits ETH entry to `msg.value` paths only, so ETH cannot be force-deposited by an attacker. The attack window only opens when a user makes this mistake.

This is the same known pattern as Uniswap v3 periphery. For Sherlock, this typically lands at **Medium** (direct fund loss conditioned on user error), not High. The claimed HIGH severity is overstated because:
- No unprivileged path forces ETH onto the router without user cooperation
- The attacker cannot manufacture the precondition; they can only exploit it after user error

---

### Title
Unguarded `refundETH()` allows any caller to steal ETH stranded on the router by a prior user's excess `msg.value` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.refundETH()` transfers `address(this).balance` to `msg.sender` with no access control. If a user sends excess ETH via `multicall{value: X}` without appending a `refundETH()` call, the unused ETH persists on the router across transactions and can be claimed by any subsequent caller.

### Finding Description
`refundETH()` is `external payable` with no caller restriction:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

The `pay()` function consumes exactly `amountIn` of the router's native balance when `nativeBalance >= value`, leaving the surplus as raw ETH on the contract. Because `receive()` blocks direct ETH deposits (only WETH can send ETH), the only source of stranded ETH is a user's own `msg.value` overshoot. If the user omits `refundETH()` from their multicall, the surplus persists and is claimable by anyone.

### Impact Explanation
Direct loss of user ETH. An attacker monitoring the chain for transactions that leave a non-zero ETH balance on the router can immediately call `refundETH()` in the next block and receive the full balance. No privileged access is required.

### Likelihood Explanation
Requires user error (omitting `refundETH()` from a multicall with excess `msg.value`). The `receive()` guard prevents the attacker from manufacturing the precondition. Likelihood is **Medium** — the pattern is common enough in production integrations that omissions occur, but it is not guaranteed.

### Recommendation
Track per-caller ETH deposits (e.g., a transient storage mapping `msg.sender → deposited`) and restrict `refundETH()` to return only the caller's own deposited amount, or enforce that `refundETH()` can only be invoked as part of a multicall in the same transaction that deposited the ETH.

### Proof of Concept
```solidity
// Victim sends 2 ether but only needs 1000 wei for the swap, forgets refundETH()
vm.prank(victim);
bytes[] memory calls = new bytes[](1);
calls[0] = abi.encodeWithSelector(router.exactInputSingle.selector, params); // amountIn=1000
router.multicall{value: 2 ether}(calls);
// 2 ether - 1000 wei is now stranded on the router

// Attacker drains it
uint256 attackerBefore = attacker.balance;
vm.prank(attacker);
router.refundETH();
assertGt(attacker.balance, attackerBefore); // attacker received victim's ETH
assertEq(address(router).balance, 0);
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
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
