### Title
Native-fee dispatch swallows Uniswap ETH refunds, permanently trapping user overpayment - (File: evm/src/core/EvmHost.sol)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest` all convert user-supplied native token into the protocol `feeToken` via `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(fee, path, address(this), block.timestamp)`. [1](#0-0) [2](#0-1) [3](#0-2)  This mirrors the external report's core defect — an on-chain swap invoked without properly accounting for execution-price variance — except here the mishandled leg is the refund path of an exact-output Uniswap V2 swap rather than a missing `amountOutMin`.

### Finding Description
`swapETHForExactTokens` on UniswapV2Router02 requires `amountOut <= msg.value`-equivalent input, executes the swap, and refunds any unspent ETH via `TransferHelper.safeTransferETH(msg.sender, msg.value - amounts[0])`. Because `EvmHost` itself is the caller of the router (not the original `_msgSender()` who funded the transaction), that refund lands on `msg.sender` from the router's perspective — i.e., the `EvmHost` contract's own balance — not on the address that actually supplied the native token to `dispatch`/`fundRequest`.

Docs explicitly acknowledge quoting native cost off-chain is imprecise and warn against depending on it on-chain because of sandwich/slippage risk, telling integrators to estimate fees off-chain before submitting `msg.value`. Any user who follows this guidance and adds a safety buffer to `msg.value` (to avoid `EXCESSIVE_INPUT_AMOUNT`-style reverts from price movement between quoting and execution) will have that buffer silently swept into `EvmHost`'s own balance rather than returned to them. Reviewing the three call sites (`dispatch(DispatchPost)`, `dispatch(DispatchGet)`, `fundRequest`), none forward, track, or refund this dust back to `_msgSender()`. [1](#0-0) [2](#0-1) [3](#0-2) 

Existing guards do not stop this: `notFrozen` only gates protocol pause state, and there is no other accounting structure (e.g., `_requestCommitments`) that tracks or exposes the residual ETH for withdrawal by the payer — the corrupted/lost value is the delta `msg.value - amounts[0]` for every native-paying call.

### Impact Explanation
Every unprivileged caller who dispatches a POST/GET request or funds a pending request with native token and supplies `msg.value` even slightly above the exact Uniswap-computed input requirement permanently loses that excess to the `EvmHost` contract — a direct, code-confirmed loss of user funds with no recovery path exposed to the payer. Because on-chain quoting is explicitly discouraged (sandwich-prone) and off-chain estimates can never be pixel-exact against the block's actual pool state, overpayment (and thus loss) is the expected, not edge-case, outcome for any conscientious integrator who pads `msg.value`.

### Likelihood Explanation
High. This triggers on the normal, documented usage pattern (native-token dispatch) without requiring a malicious relayer, prover, governance actor, or front-running; the mismatch between the router's refund recipient (`address(this)`, i.e. `EvmHost`) and the true payer (`_msgSender()`) fires on every native dispatch/fundRequest call where `msg.value` isn't the exact wei amount the pool happens to require at execution time.

### Recommendation
Track the pre/post native balance around the `swapETHForExactTokens` call and forward any residual ETH back to `_msgSender()` (or `post.payer`/`get.payer`) instead of letting the router's refund settle on the `EvmHost` contract, e.g.:
```solidity
uint256 balanceBefore = address(this).balance - msg.value;
IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(post.fee, path, address(this), block.timestamp);
uint256 dust = address(this).balance - balanceBefore;
if (dust > 0) payable(_msgSender()).transfer(dust);
```

### Proof of Concept
1. Compute `amounts[0] = getAmountsIn(fee, [WETH, feeToken])` off-chain and add a safety buffer (standard practice, and the only way to avoid `dispatch` reverting on adverse price movement given the docs' warning against on-chain `quote()`).
2. Call `EvmHost.dispatch{value: amounts[0] + buffer}(post)`.
3. Internally, `swapETHForExactTokens{value: msg.value}(post.fee, path, address(this), block.timestamp)` executes; the router refunds `buffer` wei to `address(this)` (`EvmHost`), not to the caller. [1](#0-0) 
4. Inspect `EvmHost`'s ETH balance after the call — it increased by `buffer`; the caller's transaction receipt shows no ETH returned. The buffer is now unrecoverable by the original payer through any function present in `EvmHost.sol`.

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

**File:** evm/src/core/EvmHost.sol (L1031-1040)
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
```
