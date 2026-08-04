## Analysis

**Core broken invariant from the report:** an intermediary contract in a cross-chain fee-payment path receives an *excess native-token refund* it has no mechanism to forward to the actual payer, permanently trapping user funds inside a contract that isn't meant to hold balances.

**Local analog found:** `EvmHost.dispatch()` / `fundRequest()` pay relayer fees by swapping `msg.value` for an exact amount of `feeToken()` through a configurable `_hostParams.uniswapV2` router, relying on the real Uniswap V2 `swapETHForExactTokens` semantics (exact-output swap that auto-refunds unused ETH to the caller). On Gnosis, that router slot is filled by `GnosisUniswapV2Interface`, which does **not** implement exact-output or refund semantics at all — it converts the *entire* `msg.value` and hands the full amount to the caller (`EvmHost`), silently discarding the `amountOut` parameter. [1](#0-0) [2](#0-1) 

### Title
Gnosis fee-swap wrapper silently swallows overpaid native ETH into `EvmHost` with no refund or reclaim path - (File: `evm/src/utils/uniswapv2/GnosisUniswapV2Wrapper.sol`)

### Summary
`EvmHost.dispatch()`, `dispatch(DispatchGet)`, and `fundRequest()` all pay relayer fees the same way: if `msg.value > 0`, they call `IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(post.fee, path, address(this), block.timestamp)`, expecting the router to take only `post.fee` worth of ETH and refund the rest to the caller (`EvmHost`), as real Uniswap V2 routers do. [1](#0-0) 

For Gnosis deployments, `_hostParams.uniswapV2` is set to `GnosisUniswapV2Interface`, whose `swapETHForExactTokens` ignores `amountOut` entirely: it wraps the *whole* `msg.value` and transfers that whole amount of wrapped native token (feeToken) to `msg.sender` (i.e., `EvmHost`). [2](#0-1) 

### Finding Description
`EvmHost.dispatch()` only credits `post.fee` to the request's `FeeMetadata` — that's the only amount tracked as reclaimable/refundable on timeout:
```
_requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: post.fee});
``` [3](#0-2) 

Any ETH sent above `post.fee` is expected to bounce back to the caller via the router's built-in exact-output refund. On real Uniswap V2 routers this happens automatically. `GnosisUniswapV2Interface.swapETHForExactTokens`, however, has no such refund branch — it converts and forwards the entire `msg.value`, not just `amountOut`:
```solidity
function swapETHForExactTokens(uint256 amountOut, address[] calldata, address, uint256)
    external payable returns (uint256[] memory)
{
    if (amountOut > msg.value) revert MsgValueLessThanExactAmount();
    (bool sent,) = WETH().call{value: msg.value}("");
    if (!sent) revert DepositFailed();
    IERC20(WETH()).safeTransfer(msg.sender, msg.value);
    ...
}
``` [2](#0-1) 

The excess feeToken thus lands as an untracked balance inside `EvmHost`. `dispatch()`/`fundRequest()` never check `msg.value == post.fee`/`amount`, and `EvmHost` has no per-payer overpayment ledger to reclaim this delta — the only fee accounting is the single `fee` field per commitment, which only ever equals `post.fee`, not the actual (larger) amount the host received. The overpaying caller — an ordinary dApp or end user dispatching a POST/GET request or funding one — has no path to recover the difference, mirroring exactly the "no way for the intermediate contract to return excess native funds to the rightful payer" pattern from the external Decent report, except here the funds accumulate inside the core `EvmHost` contract itself rather than a bridge adapter.

### Impact Explanation
Every native-token payer of `dispatch()`/`fundRequest()` on a Gnosis-configured Hyperbridge deployment permanently loses any ETH sent above the exact relayer fee required, since the router used for that chain does not implement the refund semantics `EvmHost` relies on. This is a straightforward, unprivileged loss-of-funds path reachable by any caller who slightly overestimates or over-supplies `msg.value` (a common and expected occurrence, since callers typically cannot know the exact `feeToken` price at call time). This falls squarely under "stealing or loss of funds" in the bounty's impact gate.

### Likelihood Explanation
High for any Gnosis-chain integration: `dispatch()`/`fundRequest()` are the primary public entry points application contracts (including Hyperbridge's own `HyperApp.dispatchWithFeeToken` callers who instead pay in native token) use to submit cross-chain messages, and slight ETH overpayment is the normal case rather than the exception because the caller must estimate the required native value before the swap executes on-chain.

### Recommendation
Make `GnosisUniswapV2Interface.swapETHForExactTokens` refund any `msg.value` beyond `amountOut` to `msg.sender`, matching the real Uniswap V2 router's exact-output behavior that `EvmHost.dispatch()`/`fundRequest()` assume; alternatively, have `EvmHost` explicitly compute and refund `msg.value - post.fee` cost basis directly to `_msgSender()` after the swap rather than trusting the router.

### Proof of Concept
1. Deploy `EvmHost` on Gnosis with `_hostParams.uniswapV2 = GnosisUniswapV2Interface`.
2. A user calls `dispatch(DispatchPost)` with `post.fee = 1e18` and sends `msg.value = 2e18` (a reasonable overestimate since exact fee-token pricing is not known client-side).
3. Inside `dispatch()`, `swapETHForExactTokens{value: 2e18}(1e18, ...)` is invoked on the Gnosis wrapper.
4. The wrapper wraps the full `2e18` native token to WETH and transfers all `2e18` WETH to `EvmHost`, per `evm/src/utils/uniswapv2/GnosisUniswapV2Wrapper.sol` lines 39-54 — no refund of the extra `1e18` occurs.
5. `EvmHost` only records `fee: 1e18` in `_requestCommitments[commitment]`; the extra `1e18` feeToken sits in `EvmHost`'s balance with no reference to the user, and no function exists to let the user reclaim it — it is permanently lost to them.

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

**File:** evm/src/core/EvmHost.sol (L946-948)
```text
        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: post.fee});
```

**File:** evm/src/utils/uniswapv2/GnosisUniswapV2Wrapper.sol (L39-54)
```text
    function swapETHForExactTokens(uint256 amountOut, address[] calldata, address, uint256)
        external
        payable
        returns (uint256[] memory)
    {
        if (amountOut > msg.value) revert MsgValueLessThanExactAmount();

        (bool sent,) = WETH().call{value: msg.value}("");
        if (!sent) revert DepositFailed();

        IERC20(WETH()).safeTransfer(msg.sender, msg.value);

        uint256[] memory out = new uint256[](1);
        out[0] = msg.value;
        return out;
    }
```
