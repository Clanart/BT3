## Finding: Unbounded native-to-feeToken AMM swap in EvmHost's public dispatch/fundRequest entrypoints, with no belief-price/slippage guard and no verified refund path to the caller

### Title
Sandwichable AMM swap on permissionless `dispatch`/`fundRequest` calls in EvmHost - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest` all let *any* caller pay the protocol fee in native ETH, which the host converts to `feeToken()` by calling the local `UniswapV2Router02.swapETHForExactTokens` directly on-chain, exactly as the "sweep" function did in the source Anchor report — with no belief price, no external oracle bound, and `amountInMaximum` implicitly set to the caller's entire `msg.value`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The pattern is structurally identical to the reported "sweep" bug: a public, unauthenticated entrypoint (`dispatch`, `fundRequest` — callable by anyone with no relayer/admin/prover involvement) triggers an on-chain AMM trade (`swapETHForExactTokens`) against the local `UniswapV2Router02` pool for `WETH → feeToken()`, with:
- No `belief_price` / oracle reference for the WETH/feeToken pair.
- No independently-derived `amountOutMin`/max-spread bound — the trade is "exact tokens out" priced entirely by the current pool state at call time.
- `deadline = block.timestamp`, which provides no MEV-timing protection since it's satisfied by construction, not an actual bound on execution price.

Any unprivileged actor can therefore manipulate the WETH/`feeToken()` pool immediately before a victim's `dispatch{value: ...}()` call lands (classic sandwich: buy feeToken pre-trade, let the host's swap execute at the worse price, sell back post-trade), extracting value from whatever ETH the victim supplied. This mirrors the report's exact root cause (`sweep` calling astroport with no `belief_price`) mapped onto Hyperbridge's fee-collection path in `EvmHost`.

### Impact Explanation
Per the Hyperbridge Impact Gate, this qualifies as "logic attacks" / "transaction manipulation" against a production bridge contract rather than a generic DeFi price-impact nuisance: the swap is embedded directly in the message-dispatch path that every cross-chain request/response on the EVM side depends on, so degraded execution price here directly reduces the fee/relayer-incentive balance recorded for the request (`FeeMetadata`), and any ETH consumed beyond fair value is value extracted from the bridge's users rather than the intended relayer-fee accounting. This is not a "front-run-only" scenario (excluded by the pivots) — it is a full sandwich requiring both a pre-trade and a post-trade leg, which was exactly the pattern accepted (with reduced-but-real severity) in the source report.

### Likelihood Explanation
Likelihood is bounded by the liquidity/depth of the specific host-configured `uniswapV2` WETH/feeToken pool and by the fact the exploit requires a victim to call `dispatch`/`fundRequest` with `msg.value` in the same block/near-block window — no privileged actor, relayer, or prover collusion is needed, satisfying the "public-entrypoint / unprivileged attacker" requirement of the Method section.

### Recommendation
Add a caller-supplied or oracle-derived minimum-output/maximum-price bound (analogous to the C4 report's recommendation to source `belief_price` from an oracle) for the native-to-feeToken conversion in `dispatch`/`fundRequest`, and bound the allowable slippage against a trusted price reference rather than trusting the instantaneous pool state unconditionally.

### Proof of Concept
1. Attacker observes a pending `dispatch{value: X}(post)` (or `fundRequest`) transaction in the mempool targeting `EvmHost`.
2. Attacker front-runs by buying `feeToken()` on the same `uniswapV2` pool the host uses, worsening the WETH→feeToken price.
3. Victim's transaction executes `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(post.fee, path, address(this), block.timestamp)` at the manipulated price — see [1](#0-0) , consuming more of `msg.value` than fair value would require, with no `belief_price`/oracle check to reject the bad fill.
4. Attacker back-runs by selling the `feeToken()` bought in step 2, capturing the price impact as profit — the same three-message sandwich structure described in the source report's PoC.

Note: I was unable to fully confirm within the available index whether any excess ETH from the router's swap is forwarded back to the original `dispatch`/`fundRequest` caller (a `receive()`/refund path) or is simply retained by `EvmHost`; this affects whether the loss is "value extracted via bad price" only, or also includes a stuck/un-refunded native-ETH component. A Devin session with full repository access would be needed to trace this refund path definitively.

### Citations

**File:** evm/src/core/EvmHost.sol (L921-932)
```text
    function dispatch(DispatchPost memory post) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                post.fee, path, address(this), block.timestamp
            );
        } else if (post.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), post.fee);
        }
```

**File:** evm/src/core/EvmHost.sol (L974-985)
```text
    function dispatch(DispatchGet memory get) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                get.fee, path, address(this), block.timestamp
            );
        } else if (get.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), get.fee);
        }
```

**File:** evm/src/core/EvmHost.sol (L1031-1042)
```text
    function fundRequest(bytes32 commitment, uint256 amount) external payable notFrozen {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                amount, path, address(this), block.timestamp
            );
        } else {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), amount);
        }
```
