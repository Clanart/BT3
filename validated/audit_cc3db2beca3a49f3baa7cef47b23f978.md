Audit Report

## Title
Unvalidated `msg.value` in non-WETH swap and liquidity functions allows ETH to be stolen via `refundETH()` — (File: `metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary
All four swap entry-points and all four liquidity entry-points are declared `payable` but contain no `msg.value == 0` guard when the input token is not WETH. Any ETH attached to such a call is silently left on the contract because `PeripheryPayments.pay()` falls through to the ERC-20 `safeTransferFrom` branch and never touches `msg.value`. The stranded ETH is immediately claimable by any address via the unrestricted `refundETH()` helper.

## Finding Description
`PeripheryPayments.pay()` branches on the token address:

```
if (payer == address(this))   → safeTransfer (ERC-20 only)
else if (token == WETH)       → deposit native ETH + transfer WETH
else                          → safeTransferFrom (ERC-20 only, msg.value untouched)
```

When `tokenIn` is any ERC-20 other than WETH the `else` branch fires, the swap or liquidity add completes successfully using `safeTransferFrom`, and any ETH that was attached to the call remains on the contract.

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only intercepts bare ETH transfers with no calldata. It is never invoked when ETH is attached to a named function call such as `exactInputSingle{value: 1 ether}(...)`, so it provides no protection here.

`refundETH()` is completely unrestricted:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // no access control
    }
}
```

Any EOA or contract can call it in the same block as the victim's transaction and receive the entire ETH balance of the router or liquidity adder.

Affected `payable` entry-points with no `msg.value == 0` guard:

| Contract | Function | Line |
|---|---|---|
| `MetricOmmSimpleRouter` | `exactInputSingle` | 67 |
| `MetricOmmSimpleRouter` | `exactInput` | 92 |
| `MetricOmmSimpleRouter` | `exactOutputSingle` | 130 |
| `MetricOmmSimpleRouter` | `exactOutput` | 154 |
| `MetricOmmPoolLiquidityAdder` | `addLiquidityExactShares` (×2) | 56, 71 |
| `MetricOmmPoolLiquidityAdder` | `addLiquidityWeighted` (×2) | 88, 123 |

## Impact Explanation
A user who accidentally attaches ETH to a non-WETH swap or liquidity call loses that ETH permanently. The ETH is not returned by the swap or liquidity logic, and any unprivileged address can immediately drain it by calling `refundETH()`. The loss is direct principal loss with no protocol recourse. The `MetricOmmPoolLiquidityAdder` has no native-ETH consumption path at all, making every `payable` entry-point on that contract a potential ETH trap. This meets the Sherlock threshold for direct loss of user principal.

## Likelihood Explanation
The functions are intentionally `payable` to support the ETH-input pattern (`multicall{value}(exactInputSingle(..., tokenIn=WETH, ...))`). Users familiar with this pattern may inadvertently attach ETH when switching to an ERC-20 input swap. Front-running bots routinely monitor for stranded ETH on well-known router addresses, so the window between the victim's transaction and theft is effectively zero. No privileged access is required; the only precondition is that the victim attached a nonzero `msg.value` to a non-WETH call.

## Recommendation
Add a `msg.value == 0` guard at the top of each function whose token path cannot consume native ETH. The cleanest approach is a shared modifier:

```solidity
modifier noNativeValue() {
    require(msg.value == 0, "unexpected msg.value");
    _;
}
```

Apply it unconditionally to all four `addLiquidityExactShares` / `addLiquidityWeighted` overloads (the liquidity adder has no native-ETH consumption path). For the router, apply it when `tokenIn != WETH` is statically known, or add an inline check before the swap call. Alternatively, keep the functions `payable` but add the check inline:

```solidity
if (params.tokenIn != WETH) require(msg.value == 0, "unexpected msg.value");
```

## Proof of Concept

```solidity
// Assume: pool is a valid USDC/DAI pool (no WETH token).
// Victim accidentally sends 1 ETH with a USDC→DAI exactInputSingle call.

// Step 1 – Victim's transaction (accidental ETH attached):
router.exactInputSingle{value: 1 ether}(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(usdcDaiPool),
        tokenIn:          address(usdc),   // NOT WETH
        tokenOut:         address(dai),
        zeroForOne:       true,
        amountIn:         1_000e6,
        amountOutMinimum: 0,
        recipient:        victim,
        deadline:         block.timestamp + 60,
        priceLimitX64:    0,
        extensionData:    ""
    })
);
// pay() takes USDC via safeTransferFrom; the 1 ETH sits on the router.
// assert(address(router).balance == 1 ether);

// Step 2 – Attacker's transaction (any EOA, same or next block):
vm.prank(attacker);
router.refundETH();
// refundETH sends address(this).balance → msg.sender (attacker).
// assert(attacker.balance increased by 1 ether);
// assert(address(router).balance == 0);
```

The swap succeeds, the victim receives DAI, and the attacker receives the victim's 1 ETH. No privileged access is required.