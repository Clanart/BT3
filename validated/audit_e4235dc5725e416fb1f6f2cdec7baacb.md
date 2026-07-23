Audit Report

## Title
Stranded Router ETH Consumed by Subsequent WETH-Input Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay` uses `address(this).balance` as the source of funds when settling a WETH-input swap, with no per-call ETH accounting. If a prior `multicall` caller omitted `refundETH`, their leftover ETH sits on the router and is silently spent to settle the next caller's WETH swap, bypassing any WETH `transferFrom` on the legitimate payer. The prior caller's ETH is permanently lost with no recourse.

## Finding Description
`pay` branches on `token == WETH` and reads the router's total native balance as `nativeBalance`: [1](#0-0) 

When `nativeBalance >= value`, the function wraps exactly `value` ETH and transfers the resulting WETH to the pool — the `payer` argument is never consulted: [2](#0-1) 

`refundETH` is a separate, optional call that sweeps all router ETH to `msg.sender`: [3](#0-2) 

`multicall` is payable and imposes no requirement to include `refundETH`, so ETH can be stranded: [4](#0-3) 

`exactInputSingle` stores `msg.sender` as the payer and `params.tokenIn` as the token in transient callback context, but when the callback fires and `pay` is reached, the payer is ignored entirely if `nativeBalance >= value`: [5](#0-4) [6](#0-5) 

**Exploit path:**
1. User A: `router.multicall{value: 1 ether}([exactInputSingle(tokenIn=WETH, amountIn=0.9 ether, ...)])` — omits `refundETH`. Router retains 0.1 ETH.
2. User B: `router.exactInputSingle{value: 0}(tokenIn=WETH, amountIn=0.1 ether, ...)`.
3. Pool callback fires → `pay(WETH, userB, pool, 0.1 ether)`.
4. `nativeBalance = 0.1 ether >= value = 0.1 ether` → router wraps User A's 0.1 ETH and sends WETH to pool.
5. User B's WETH allowance is never touched. User A's 0.1 ETH is gone.

No existing guard prevents this: `receive()` only blocks non-WETH ETH senders, and the callback pool check only validates the caller identity, not the ETH source.

## Impact Explanation
Direct loss of user principal. User A's ETH is consumed to settle User B's swap. User B pays zero WETH from their wallet. The router ends at zero balance and User A has no on-chain recourse. This is a direct principal loss meeting Sherlock High severity.

## Likelihood Explanation
Omitting `refundETH` in a multicall is a common integration mistake. The pattern is exploitable by any unprivileged caller who observes router ETH balance (e.g., via mempool monitoring or on-chain balance check) and submits a WETH `exactInputSingle` for exactly `router.balance`. No special permissions, malicious pool, or non-standard tokens are required. MEV bots can front-run the victim's own `refundETH` call.

## Recommendation
Track per-call ETH credit in transient storage: set it to `msg.value` at the entry of each top-level call, decrement it as ETH is consumed inside `pay`, and use only that credit — not `address(this).balance` — when deciding whether to wrap ETH. Alternatively, revert inside `pay` if `address(this).balance > 0` and `msg.value == 0` for the current call, preventing cross-user ETH consumption entirely.

## Proof of Concept
```solidity
// 1. User A multicalls with 1 ETH, swaps 0.9 ETH worth of WETH, omits refundETH
router.multicall{value: 1 ether}(
    [abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
        tokenIn: WETH, amountIn: 0.9 ether, ...
    })))]
);
// router.balance == 0.1 ether (stranded)

// 2. User B swaps with tokenIn=WETH, sends no ETH
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: WETH, amountIn: 0.1 ether, ...
}));
// pay(WETH, userB, pool, 0.1 ether) called
// nativeBalance (0.1e18) >= value (0.1e18) → wraps User A's ETH, no transferFrom userB

// Assert:
assert(router.balance == 0);          // User A's ETH consumed
assert(userA_eth_lost == 0.1 ether);  // no recourse
assert(userB_weth_spent == 0);        // paid nothing from wallet
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
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
