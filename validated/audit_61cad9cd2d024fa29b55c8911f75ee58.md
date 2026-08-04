### Title
Native-token overpayment on `EvmHost.dispatch()`/`fundRequest()` is trapped by AMM spot-price swaps with no refund path to the payer - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `dispatch(DispatchGet)`, and `fundRequest()` convert a caller's native-token payment into `feeToken` by calling Uniswap V2's `swapETHForExactTokens` with the **instantaneous spot price** of the configured pool, with no TWAP, no price bound, and `deadline = block.timestamp` (i.e. no real deadline protection). [1](#0-0)  The same pattern repeats for `DispatchGet` and `fundRequest`. [2](#0-1) [3](#0-2)  Because `swapETHForExactTokens` refunds any unspent `msg.value` to `msg.sender` of the router call — which is `EvmHost` itself, not the original dispatching account — any leftover native token from a price move between quote-time and execution-time is absorbed into the Host contract's own balance rather than returned to the user who sent it. `EvmHost` exposes no per-caller refund path for this ETH; the only way it can move is via governance's `withdraw()` of "bridge revenue" through the `hostManager`. [4](#0-3) 

### Finding Description
This is the same broken invariant as the external report: a fund-moving decision (how much native token is required/consumed for a given `feeToken` amount) is derived from a single, un-averaged, manipulable instantaneous price rather than any time-weighted or bounded reference. In the TWAP report, `GetLatestPriceFromAnySource` let a single stale/manipulable tick decide state; here, the single current Uniswap V2 reserve ratio at the moment of the swap decides how much of the caller's `msg.value` is consumed versus refunded-into-the-Host.

`dispatch()` performs no `amountInMax` check and no TWAP-derived sanity bound on the swap — it simply hands the router `msg.value` and lets the AMM's current tick decide the split between "consumed" and "refunded." An attacker who sandwiches a victim's `dispatch()`/`fundRequest()` call (a normal, unprivileged front-run — no malicious relayer, prover, or admin required) can push the WETH/`feeToken` pool price in either direction:
- Push the price so that more WETH than the victim expected is required to buy `post.fee`/`get.fee` tokens: the transaction still succeeds if the victim over-provisioned `msg.value` as a buffer, but the excess is absorbed by the Host contract balance instead of returning to the victim.
- Because there is no deadline (`block.timestamp` is always satisfied) and no `amountInMax`, the swap always executes at whatever price exists at that exact block, so the attacker fully controls how much of the victim's `msg.value` becomes "trapped protocol balance" versus refunded — except the refund destination is wrong in all cases (goes to `address(this)`, i.e. the Host, not the caller).

Existing guards do not stop this: there is no slippage bound relative to a TWAP, no refund-to-caller logic, and the only sweep of the resulting Host ETH balance is `hostManager.withdraw()`, a privileged governance path that pays out to whatever beneficiary governance designates — never back to the individual user who overpaid. [4](#0-3) 

### Impact Explanation
Every unprivileged user who dispatches a POST/GET request or funds a pending request with native token (the documented, first-class payment path — see `docs/content/developers/evm/messaging/post-requests.mdx`) risks permanent loss of any amount of `msg.value` beyond what the AMM's spot price consumes at execution time. [5](#0-4)  This is a real, protocol-level loss-of-funds bug: the lost ETH is not returned to its owner and is only recoverable by a privileged withdrawal to an arbitrary beneficiary chosen by governance, not the original payer.

### Likelihood Explanation
Any dispatcher supplying `msg.value` with even a small buffer for gas-price/slippage safety — which is the norm, since exact spot amounts are volatile between quote and execution — will systematically leave dust or larger residuals trapped. An attacker can amplify the trapped amount deterministically by sandwiching the transaction on any pool with moderate depth, requiring no privileged role, no relayer collusion, and no consensus assumptions — purely public mempool front-running against a public, permissionless entrypoint (`dispatch`/`fundRequest`).

### Recommendation
- Compute the required native input via the router's `getAmountsIn` (or a TWAP-based estimate) before swapping, pass an explicit `amountInMax`/refund-safe bound, and revert on unacceptable slippage instead of silently letting the AMM decide the split.
- Explicitly forward any unspent `msg.value` back to `_msgSender()` after the swap rather than letting it sit in the Host contract's balance.
- Consider using a TWAP oracle (or a bounded max-deviation check against one) for the native/feeToken conversion rate used in `dispatch()`/`fundRequest()`, consistent with the report's core recommendation, instead of relying on the pool's instantaneous spot price.

### Proof of Concept
1. A user calls `EvmHost.dispatch(DispatchPost)` with `post.fee = F` and `msg.value = V`, where `V` is sized with a buffer above the last-observed spot-price quote for `F` feeToken. [1](#0-0) 
2. An attacker observes the pending transaction and front-runs it with a swap on the same WETH/`feeToken` Uniswap V2 pool that lowers the WETH cost of buying `F` feeToken (or simply lets the pool's natural price drift do the same across blocks).
3. `swapETHForExactTokens(F, path, address(this), block.timestamp)` executes, consuming less than `V` WETH-equivalent; the router refunds `V - amountUsed` in native token to `msg.sender`, which is the `EvmHost` contract itself, not the user.
4. The user's excess ETH becomes part of the Host's native balance. `EvmHost` has no user-facing refund/sweep function; the only withdrawal path is `hostManager.withdraw()`, callable exclusively by the privileged `hostManager` module, paying to a beneficiary of governance's choosing. [4](#0-3) 
5. Repeating this for every over-provisioned `dispatch()`/`fundRequest()` call accumulates unrecoverable user funds inside the Host contract, permanently lost to the original payer.

### Citations

**File:** evm/src/core/EvmHost.sol (L74-96)
```text
interface IHostManager {
    /**
     * @dev Updates IsmpHost params
     * @param params new IsmpHost params
     */
    function updateHostParams(HostParams memory params) external;

    /**
     * @dev withdraws bridge revenue to the given address
     * @param params, the parameters for withdrawal
     */
    function withdraw(WithdrawParams memory params) external;
}

// Withdrawal parameters
struct WithdrawParams {
    // The beneficiary address
    address beneficiary;
    // the amount to be disbursed
    uint256 amount;
    // Withdraw the native token?
    address token;
}
```

**File:** evm/src/core/EvmHost.sol (L908-932)
```text
    /**
     * @dev Dispatch a POST request to Hyperbridge
     *
     * @notice Payment for the request can be made with either the native token or the feeToken.
     * If native tokens are supplied, it will perform a swap under the hood using the local uniswap router.
     * Will revert if enough native tokens are not provided.
     *
     * If no native tokens are provided then it will try to collect payment from the calling contract in
     * the feeToken.
     *
     * @param post - post request
     * @return commitment - the request commitment
     */
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

**File:** evm/src/core/EvmHost.sol (L1031-1041)
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
```
