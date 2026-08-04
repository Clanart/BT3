### Title
Unsafe deadline (`block.timestamp`) in `EvmHost` native-token-to-feeToken swaps disables MEV/staleness protection for request fees - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatch(DispatchPost)`, `EvmHost.dispatch(DispatchGet)`, and `EvmHost.fundRequest()` all convert native token payment into `feeToken` via a Uniswap V2 `swapETHForExactTokens` call, and all three pass `block.timestamp` as the swap `deadline`. This is the same anti-pattern flagged in the external report: passing the current execution-time timestamp as `deadline` makes the deadline check `block.timestamp <= block.timestamp`, which is always true no matter how long the transaction sits in the mempool before being mined. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`dispatch(DispatchPost)` builds a swap path `[WETH, feeToken]` and calls `IUniswapV2Router02.swapETHForExactTokens{value: msg.value}(post.fee, path, address(this), block.timestamp)`: [1](#0-0) 

The same pattern repeats verbatim in `dispatch(DispatchGet)`: [2](#0-1) 

and in `fundRequest()`, which lets anyone top up the relayer fee escrowed for a pending request: [3](#0-2) 

Because `deadline` is evaluated as `block.timestamp` at the moment the transaction actually executes (not at signing/submission time), the Uniswap router's deadline check can never revert a stale transaction — this defeats the entire purpose of the deadline parameter. A transaction paying the native-token fee can be held out of the mempool for an arbitrary amount of time (deliberately delayed inclusion, private-mempool/MEV-builder collusion, or simple network congestion) and still be executed at whatever price prevails when finally included, with no staleness protection whatsoever. This exactly mirrors the external report's core broken invariant: "max/no-op deadline" removes the safety property the parameter is meant to provide.

Unlike an ordinary end-user DEX interaction, this call sits inside Hyperbridge's core protocol path for paying cross-chain message relayer fees (`dispatch`) and for funding pending request fees (`fundRequest`). The amount swapped (`post.fee` / `get.fee` / `amount`) is an exact-output amount denominated in `feeToken`; the `amountInMax` is implicitly `msg.value`. A delayed-inclusion + adverse price movement scenario can force the contract to spend materially more ETH than the market rate at submission time to acquire the fixed `feeToken` output, up to the full `msg.value` cap, with the difference captured by whoever controls transaction ordering (e.g., a builder/searcher sandwiching the swap once it is finally included). There is no `payer`-facing slippage parameter or ability to specify a real deadline — the caller has no way to protect themselves.

### Impact Explanation
This falls under the "transaction manipulation / logic attacks" and "loss of funds" categories from the required impact set: an unprivileged party who controls or influences block inclusion timing (e.g. builder/searcher, or simply patient adversarial ordering) can exploit the always-true deadline to execute the AMM swap at a manipulated/stale price against the message sponsor, extracting value from the fee payer's `msg.value` at the moment of execution via classic time-delay/sandwich MEV. Because this code path lives in the core `EvmHost` contract used on every EVM deployment for native-token fee payment, it affects any user or application dispatching POST/GET requests or funding requests with native currency — a broad, protocol-level surface, not a peripheral utility contract.

### Likelihood Explanation
Likelihood is moderate: exploitation requires the attacker to control or manipulate the timing of transaction inclusion (a searcher/builder capability), which is a normal MEV actor capability rather than a privileged Hyperbridge role, and does not require a malicious relayer, prover, or governance action. Given that `msg.value`-based native payments are a documented, first-class payment method for `dispatch`, this path will be exercised regularly in production, and the deadline bypass is unconditional (present on every call, not a rare edge case).

### Recommendation
Do not use `block.timestamp` as the deadline for the internal Uniswap swap. Either:
1. Accept a caller-supplied `deadline` parameter (validated to be `>= block.timestamp` and reasonably bounded) in `DispatchPost`/`DispatchGet`/`fundRequest`, and forward it to `swapETHForExactTokens`, or
2. Perform an off-chain price/slippage check and pass a tight, short-lived deadline computed at call time, and add a caller-specified maximum acceptable ETH spend (`amountInMax`) distinct from raw `msg.value`, refunding any unused ETH.

This mirrors the Mellow/Cantina fix pattern from the external report: let the caller supply and validate the deadline rather than embedding a value that trivially always passes.

### Proof of Concept
1. User (or app) calls `EvmHost.dispatch(DispatchPost)` with `msg.value` set to cover the expected ETH cost of acquiring `post.fee` feeTokens, expecting near-immediate execution at current market price. [1](#0-0) 
2. A builder/searcher observing the pending transaction delays its inclusion (e.g., via private order flow, or simply because gas price is not competitive) while manipulating or waiting for adverse price movement in the WETH/feeToken pool.
3. When finally included, `swapETHForExactTokens(post.fee, path, address(this), block.timestamp)` computes `deadline = block.timestamp` at execution time, so the router's `require(deadline >= block.timestamp)` check trivially passes regardless of how stale the transaction is.
4. The swap executes at the manipulated/current price, consuming up to the full `msg.value` to acquire the fixed `post.fee` output; the sponsor receives no protection against price staleness and no refund mechanism recovers the value lost to the delayed, adversarially-timed execution.

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
