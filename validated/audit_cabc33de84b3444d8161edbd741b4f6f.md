Audit Report

## Title
Excess ETH stranded and stealable when `exactInputSingle` is called directly with `msg.value > amountIn` and `tokenIn=WETH` - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
When a user calls `exactInputSingle` directly (not via `multicall`) with `tokenIn=WETH` and `msg.value > params.amountIn`, the `pay()` function deposits exactly `params.amountIn` worth of native ETH as WETH and transfers it to the pool, leaving the surplus `msg.value - amountIn` ETH permanently stranded on the router. Because `refundETH()` is a public, permissionless function that sends the router's entire ETH balance to `msg.sender`, any third party can immediately steal the stranded ETH in a subsequent call.

## Finding Description

**Full call path:**

1. User calls `exactInputSingle{value: X}(params)` where `params.tokenIn = WETH` and `X > params.amountIn`.
2. `exactInputSingle` sets transient callback context: `payer = msg.sender`, `tokenToPay = WETH`.
3. `pool.swap()` is called; the pool transfers output tokens to `recipient`, then calls back `metricOmmSwapCallback`.
4. `metricOmmSwapCallback` dispatches to `_justPayCallback`, which calls:
   ```solidity
   pay(_getTokenToPay(), _getPayer(), msg.sender, uint256(...amountIn...));
   ```
5. Inside `pay()` (`PeripheryPayments.sol` lines 73–84):
   ```solidity
   } else if (token == WETH) {
       uint256 nativeBalance = address(this).balance;   // == X (full msg.value)
       if (nativeBalance >= value) {                    // X >= amountIn → TRUE
           IWETH9(WETH).deposit{value: value}();        // deposits exactly amountIn
           IERC20(WETH).safeTransfer(recipient, value); // sends amountIn WETH to pool
       }
       // X - amountIn ETH remains on the router — never refunded
   ```
6. `exactInputSingle` returns. No `refundETH()` call is made. The surplus `X - amountIn` ETH sits on the router.
7. Attacker calls `refundETH()` (public, no access control):
   ```solidity
   function refundETH() external payable override {
       uint256 balance = address(this).balance;
       if (balance > 0) { _transferETH(msg.sender, balance); }
   }
   ```
   The attacker receives the victim's surplus ETH.

**Why existing guards are insufficient:**
- `receive()` only blocks *direct* ETH transfers from non-WETH addresses; it does not prevent ETH from arriving via `msg.value` on payable swap functions.
- `refundETH()` has no caller restriction and no per-user accounting; it sweeps the entire router ETH balance to whoever calls it first.
- `exactInputSingle` is `payable` and the `pay()` function explicitly handles the `nativeBalance >= value` branch, making ETH-input a supported (not accidental) path — but the function never issues a refund for the excess.
- The test `test_mixedNativeAndWeth_exactInputSingle_wethForToken` confirms the direct-call ETH path is intentionally supported (it passes with `msg.value < amountIn`), but no test covers `msg.value > amountIn` without a subsequent `refundETH` in the same multicall.

**Exact corrupted value:** `address(router).balance` accumulates `msg.value - amountIn` ETH attributable to the victim, which `refundETH` transfers to an arbitrary `msg.sender`.

## Impact Explanation
Direct loss of user principal (native ETH). Any user who calls `exactInputSingle` directly with `msg.value > params.amountIn` and `tokenIn=WETH` loses the surplus ETH to the first caller of `refundETH()`. The loss is bounded only by how much excess ETH the victim sent; it can be arbitrarily large. This matches the "pool insolvency / direct loss of user principal" impact gate and exceeds Sherlock High thresholds for any non-dust surplus.

## Likelihood Explanation
The attack requires no special privileges. Any unprivileged attacker can monitor the mempool for `exactInputSingle` calls with `msg.value > amountIn` and `tokenIn=WETH`, then front-run or back-run with a `refundETH()` call. The victim need only make a common UX mistake (sending slightly more ETH than needed as a buffer, or using a stale quote). The pattern is repeatable on every such transaction.

## Recommendation
Add an automatic ETH refund at the end of `exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) when `tokenIn == WETH`:

```solidity
// After swap settlement, refund any unused native ETH to msg.sender
uint256 remaining = address(this).balance;
if (remaining > 0) _transferETH(msg.sender, remaining);
```

Alternatively, document and enforce that ETH-input swaps must always be wrapped in a `multicall` that includes `refundETH()` as the final step, and remove the `payable` modifier from single-function entry points that cannot safely handle excess ETH.

## Proof of Concept

```solidity
// Foundry test sketch
function test_exactInputSingle_excessEthStranded() public {
    uint128 amountIn = 1_000;
    uint256 excess = 1 ether;
    uint256 msgValue = amountIn + excess;

    address attacker = makeAddr("attacker");

    vm.prank(swapper);
    // Victim sends more ETH than amountIn
    router.exactInputSingle{value: msgValue}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: amountIn,
            amountOutMinimum: 0,
            recipient: recipient,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    // Router now holds `excess` ETH belonging to swapper
    assertEq(address(router).balance, excess);

    // Attacker steals it
    vm.prank(attacker);
    router.refundETH();
    assertEq(attacker.balance, excess);
    assertEq(address(router).balance, 0);
}
```