## Finding: Unbounded slippage in `placeOrder`'s native-fee swap enables sandwich theft of user ETH

Tracing the sandwich bug-class into `evm/src/apps/IntentGatewayV2.sol`, the closest real analog is the Uniswap V2 swap embedded in `placeOrder()` when a user pays the solver fee with native ETH.

### Title
Unbounded-slippage `swapETHForExactTokens` in `IntentGatewayV2.placeOrder` allows sandwich theft of user ETH - (File: evm/src/apps/IntentGatewayV2.sol)

### Summary
When a user places an order and funds `order.fees` with native ETH (`msgValue > 0`), the contract swaps ETH for the exact fee-token amount via the raw Uniswap V2 router, using the *entire remaining `msgValue`* as the implicit `amountInMax`, rather than a price computed from an oracle or a tight, purpose-computed bound.

### Finding Description [1](#0-0) 

```solidity
if (order.fees > 0) {
    address feeToken = IDispatcher(hostAddr).feeToken();
    if (msgValue > 0) {
        address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
        address WETH = IUniswapV2Router02(uniswapV2).WETH();
        address[] memory path = new address[](2);
        path[0] = WETH;
        path[1] = IDispatcher(hostAddr).feeToken();
        uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
            order.fees, path, address(this), block.timestamp
        );
        msgValue -= amounts[0];
    }
    ...
```

`swapETHForExactTokens` takes the exact output (`order.fees`) and an `amountInMax` — here supplied implicitly as the full `msgValue` attached to the transaction. This is exactly the pattern flagged in the external report: the contract executes a live-pool spot-price swap with no oracle/TWAP reference and no tightly computed slippage bound; the only bound is the caller's own `msg.value`.

The SDK caller sizes `msgValue` from `quoteOrderFees` as `estimate.totalGasCostWei + (estimate.totalGasCostWei * 2) / 100` — i.e., only a **2% buffer** over the expected ETH cost: [2](#0-1) 

An attacker who observes a pending `placeOrder(order, graffiti)` call with `msgValue > 0` can sandwich it:
1. Front-run: buy `feeToken` from the WETH/feeToken pool to push the WETH price of `feeToken` up.
2. The victim's `swapETHForExactTokens` executes at the inflated price, consuming close to the full `msgValue` to acquire `order.fees` worth of `feeToken` (bounded only by `amounts[0] <= msgValue`, i.e. `EXCESSIVE_INPUT_AMOUNT` revert as the sole backstop).
3. Back-run: sell `feeToken` back, capturing the price impact as profit — extracted directly from the victim's 2% ETH buffer, with any leftover refunded to the user reduced accordingly (`msgValue -= amounts[0]` then refunded via the native-refund path at the end of `placeOrder`, lines 364-368).

Unlike `SimplexPaymaster.swapAndDeposit` in the same repo — which was clearly engineered to avoid this exact class of bug by deriving `amountOutMin` from Chainlink oracles rather than the pool's own spot price (`evm/src/utils/SimplexPaymaster.sol:299-330`) — `IntentGatewayV2.placeOrder` has no such protection for its Uniswap V2 fee-funding swap.

### Impact Explanation
This is a public entrypoint (`placeOrder`) reachable by any unprivileged user, and the loss (the sandwiched ETH buffer) is extracted by an unrelated MEV attacker rather than going to its rightful recipient (the user's refund or the protocol's fee collection). It is direct loss of user funds via price manipulation of an on-chain swap performed by the protocol itself, not a front-run-only griefing or a compromised-actor scenario — the attacker needs only mempool visibility and DEX liquidity, both permissionless.

### Likelihood Explanation
Any order funded with native ETH for fees (`order.fees > 0` and `msgValue > 0`) is exposed whenever it sits in the public mempool before inclusion. The bound on attacker profit is capped by the 2% buffer the SDK provides (beyond that, `swapETHForExactTokens` reverts with `EXCESSIVE_INPUT_AMOUNT`, causing a DoS on the order instead of theft), so likelihood/impact scales with order volume and fee-token pool liquidity/depth — thinner pools make sandwiching cheaper and more profitable relative to the 2% buffer.

### Recommendation
Do not size the swap's slippage bound off the caller-supplied `msg.value`. Instead:
- Compute `amountInMax` from an oracle-derived (e.g., Chainlink) price with an explicit, governance-configured `maxSlippageBps`, mirroring the pattern already used in `SimplexPaymaster.swapAndDeposit`.
- Alternatively, let the user supply an explicit `amountInMax` (or `minOut`) parameter in the order/fill options so slippage tolerance is user-chosen rather than implicitly equal to their entire native buffer.

### Proof of Concept
1. User calls `placeOrder(order, graffiti)` with `order.fees = F` (fee-token) and `msg.value = M` sized by the SDK's `quoteOrderFees` (≈1.02× expected ETH cost).
2. Attacker sees the pending tx in the mempool and front-runs it: swaps ETH → `feeToken` on the same Uniswap V2 pool `IDispatcher(hostAddr).uniswapV2Router()` uses, pushing the ETH price of `feeToken` up.
3. Victim's `placeOrder` executes `swapETHForExactTokens{value: M}(F, [WETH, feeToken], address(this), block.timestamp)`; because there is no tight `amountInMax`, it succeeds at the inflated price, consuming up to the full `M` (as long as `amounts[0] <= M`).
4. Attacker back-runs: swaps `feeToken` → ETH, capturing the price-impact spread as profit, funded by the victim's buffer.
5. Victim receives less (or zero) native-ETH refund than expected for the same `F` fee-token amount purchased, having overpaid ETH relative to fair market price — the classic sandwich-extraction pattern from the source report, reproduced against `IntentGatewayV2.placeOrder`'s embedded Uniswap V2 swap.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L345-359)
```text
        if (order.fees > 0) {
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
                msgValue -= amounts[0];
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }
```

**File:** sdk/packages/sdk/src/protocols/intents/IntentGateway.ts (L764-773)
```typescript
		const fees = isSameChain
			? estimate.totalGasInFeeToken * 2n
			: ((estimate.totalGasInFeeToken + estimate.relayerFeeInSourceFeeToken) * 105n) / 100n

		const { address: feeToken } = await this.source.getFeeTokenWithDecimals()

		return {
			fees,
			nativeValue: estimate.totalGasCostWei + (estimate.totalGasCostWei * 2n) / 100n,
			feeToken,
```
