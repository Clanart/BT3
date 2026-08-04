### Title
Unbounded UniswapV2 fee swap in `placeOrder` lets an attacker manipulate `swapETHForExactTokens` price to drain a user's excess ETH refund via sandwiching - ([File: evm/src/apps/IntentGatewayV2.sol])

### Summary
When a user places an order and pays the `order.fees` amount in native ETH, `placeOrder` swaps ETH for an exact amount of `feeToken` via `IUniswapV2Router02.swapETHForExactTokens`, using the *entire remaining `msgValue`* as the max input, with no slippage/price-impact bound and no minimum-refund check. [1](#0-0) 

### Finding Description
`swapETHForExactTokens(order.fees, path, address(this), block.timestamp)` is called with `msgValue` (all of the caller's leftover ETH after input/predispatch escrow) as the amount of ETH made available to the router, and no `amountInMax` cap is enforced by the gateway itself — the router will consume however much ETH the current pool price requires to produce exactly `order.fees` of `feeToken`, up to the full `msgValue` supplied. [2](#0-1) 

This is the direct analog of the reported Hypervisor bug: an AMM interaction (`pool.swap`/`pool.mint`/`pool.burn`) is executed synchronously inside a user-facing function without any caller-supplied minimum-output/maximum-input guard, so the effective price paid is determined entirely by momentary pool state, which a third party can move before the transaction executes (classic sandwich). Here the corrupted value is `amounts[0]` (actual ETH consumed by the swap) and consequently `msgValue` (the ETH refunded to the user). [3](#0-2) 

After the swap, any leftover `msgValue` is refunded to `msg.sender`: [4](#0-3) 
```
if (msgValue > 0) {
    (bool sent,) = msg.sender.call{value: msgValue}("");
    if (!sent) revert InsufficientNativeToken();
}
```
An attacker who pushes the WETH/feeToken pool price up immediately before the user's `placeOrder` call (and back down immediately after) forces the router to consume far more ETH than the fair-value amount to obtain the same `order.fees` worth of `feeToken`, shrinking the user's refund and extracting the difference as sandwich profit. There is no `amountInMax`/minimum-refund parameter on `Order`/`placeOrder` for the caller to protect themselves, unlike a normal DEX integration.

### Impact Explanation
Under the Impact Gate this qualifies as "stealing or loss of funds" through "transaction manipulation": every `placeOrder` call that pays fees in native ETH is a public, unprivileged entry point, and the loss (excess ETH consumed vs. fair value) transfers value from the user to an attacker who only needs to submit ordinary transactions around the victim's — no relayer, prover, admin, or malicious peer is required, satisfying the "reject anything requiring a malicious peer/relayer/admin" constraint by not needing any of them.

### Likelihood Explanation
Likelihood is moderate-to-high in practice: it requires the WETH/feeToken UniswapV2 pool used by the configured `uniswapV2Router` to have exploitable depth/liquidity relative to `order.fees`, and it requires `order.fees > 0` and payment via native ETH (`msgValue > 0`), both of which are normal, expected usage paths documented for the Intent Gateway's fee mechanics. Any searcher watching the mempool for `placeOrder` calls with ETH fee payment can execute the sandwich with standard front/back-run bundles.

### Recommendation
Add an explicit `feeSwapMaxInput`/slippage-bound parameter to `Order`/`placeOrder` (or derive one from an oracle/TWAP with a bounded deviation), and pass it as `amountInMax` to `swapETHForExactTokens` instead of implicitly capping at the full remaining `msgValue`; revert if the quoted required input exceeds the bound rather than silently consuming more ETH. This mirrors the auditor's original recommendation of adding an `amountMin`/`amountMax` guard to swap calls.

### Proof of Concept
1. User calls `placeOrder` with `order.fees = F` (in `feeToken`), sending `msgValue = M` ETH intended to cover both `order.inputs` (if native) and the fee swap, relying on fair-price execution of `swapETHForExactTokens(F, [WETH, feeToken], address(this), block.timestamp)`.
2. Attacker front-runs with a large buy of `feeToken` (or sell of WETH) in the same UniswapV2 pool referenced by `IDispatcher(hostAddr).uniswapV2Router()`, spiking the ETH price required to obtain `F` units of `feeToken`.
3. The victim's `placeOrder` transaction executes; `swapETHForExactTokens` consumes a much larger `amounts[0]` of ETH than fair value to still deliver exactly `F` feeToken (the router only guarantees the exact output, not a bounded input, since the gateway never restricts `amountInMax`).
4. Attacker back-runs, reversing the price move and pocketing the extra ETH extracted from the victim's `msgValue`.
5. Victim's refund (`msgValue - amounts[0]`) at line 364-368 is correspondingly reduced — funds are lost to the attacker with no error raised, because the gateway performed no minimum-refund/maximum-input check. [5](#0-4)

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L345-368)
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

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }

        // Refund any unspent native tokens to the user.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
```
