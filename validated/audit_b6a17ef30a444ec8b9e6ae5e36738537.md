Audit Report

## Title
Stranded Router ETH Consumed by Subsequent WETH-Input Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay` uses `address(this).balance` as the source of funds when settling a WETH-input swap, with no per-call or per-user accounting. Because `multicall` is payable and `refundETH` is an optional, separate call, any ETH left in the router from a prior caller is silently spent on behalf of the next caller whose `tokenIn == WETH`. The prior caller's ETH is permanently lost with no recourse.

## Finding Description
In `PeripheryPayments.sol` (L73–84), when `token == WETH` and `payer != address(this)`, `pay` reads the router's entire native balance:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);   // payer is never touched
``` [1](#0-0) 

The `payer` argument is completely ignored in this branch; no WETH is pulled from the actual caller's wallet. The router's ETH — regardless of who deposited it — is wrapped and forwarded to the pool.

`refundETH` (L58–63) returns all router ETH to `msg.sender`, but it is a separate, optional call: [2](#0-1) 

`multicall` (L39–44) is `payable` and enforces no requirement to include `refundETH`: [3](#0-2) 

`exactInputSingle` stores `msg.sender` as the payer in transient context (L71), but that payer is bypassed entirely by the `nativeBalance >= value` branch: [4](#0-3) 

**Exploit path:**
1. User A calls `multicall{value: 1 ether}([exactInputSingle(tokenIn=WETH, amountIn=0.9 ether)])` omitting `refundETH`. After the swap, `router.balance == 0.1 ether`.
2. User B calls `exactInputSingle{value: 0}(tokenIn=WETH, amountIn=0.1 ether)`. Inside the swap callback, `pay(WETH, userB, pool, 0.1 ether)` is invoked. `nativeBalance (0.1 ether) >= value (0.1 ether)` is true, so the router wraps User A's 0.1 ETH and sends WETH to the pool. User B's WETH allowance is never touched.
3. `router.balance == 0`. User A has lost 0.1 ETH permanently; User B paid nothing from their wallet.

No existing guard prevents this: there is no per-call ETH credit, no check that `msg.value > 0` before using native balance, and no enforcement that `refundETH` is called.

## Impact Explanation
Direct, permanent loss of user principal (ETH). User A's stranded ETH is consumed to settle User B's swap. User A has no recourse; the router ends at zero balance. This is a direct principal loss meeting Sherlock High severity thresholds.

## Likelihood Explanation
Omitting `refundETH` in a multicall is a common integration mistake. The attack requires no special permissions, no malicious pool, and no non-standard tokens. An MEV bot can monitor the mempool for multicalls that omit `refundETH` and immediately follow with a WETH `exactInputSingle` for exactly `router.balance`, making this reliably exploitable and repeatable.

## Recommendation
Track per-call ETH credit in transient storage: set it to `msg.value` at the entry of each top-level call and decrement it as ETH is consumed inside `pay`. Use only that per-call credit — not `address(this).balance` — in the WETH branch of `pay`. Alternatively, revert in `pay` if `nativeBalance > 0` and the current call did not supply ETH (`msg.value == 0`), preventing cross-user ETH consumption entirely.

## Proof of Concept
```
// Step 1 – User A strands ETH
router.multicall{value: 1 ether}([
    abi.encodeCall(router.exactInputSingle, (
        ExactInputSingleParams({tokenIn: WETH, amountIn: 0.9 ether, ...})
    ))
    // refundETH intentionally omitted
]);
assert(address(router).balance == 0.1 ether);

// Step 2 – User B steals User A's ETH
router.exactInputSingle{value: 0}(
    ExactInputSingleParams({tokenIn: WETH, amountIn: 0.1 ether, ...})
);
// pay(WETH, userB, pool, 0.1 ether):
//   nativeBalance = 0.1 ether >= value = 0.1 ether  → true
//   router wraps 0.1 ether of User A's ETH, sends WETH to pool
//   userB's WETH.transferFrom never called

assert(address(router).balance == 0);
// User A lost 0.1 ETH; User B paid 0 WETH from wallet
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```
