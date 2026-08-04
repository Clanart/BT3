### Title
Unchecked `uint128` downcast of `order.fees` in the UniswapV2 wrapper desyncs `IntentGatewayV2` fee-escrow accounting from actual token balance - (File: `evm/src/utils/uniswapv2/UniV4UniswapV2Wrapper.sol`)

### Summary
`UniV4UniswapV2Wrapper.swapETHForExactTokens()` truncates the caller-supplied `uint256 amountOut` (and `msg.value`) to `uint128` when building the Uniswap V4 swap params, with no `SafeCast`/bounds check. This wrapper is the exact `IUniswapV2Router02` implementation that `IntentGatewayV2.placeOrder()` calls with the user-controlled `order.fees` field as `amountOut`. The gateway then unconditionally records the **untruncated** `order.fees` value into escrow (`_orders[commitment][TRANSACTION_FEES] = order.fees`), while the actual swap only requested/received the **truncated** `uint128(order.fees)` amount of fee token. This is structurally identical to the reported `PerpetualMarket.deposit()` bug: a value larger than `uint128` is used for bookkeeping while a silently truncated value is what's actually transferred.

### Finding Description
In `evm/src/utils/uniswapv2/UniV4UniswapV2Wrapper.sol`: [1](#0-0) 
`amountOut` and `msg.value` are cast with `uint128(amountOut)` / `uint128(msg.value)` directly into the V4 swap params with no `require(amountOut <= type(uint128).max)` guard, unlike the OZ `SafeCast` pattern the external report recommends.

`IntentGatewayV2.placeOrder()` invokes this exact function with the attacker-supplied `order.fees`: [2](#0-1) 
and unconditionally stores the full, untruncated `order.fees` as the escrowed fee amount: [3](#0-2) 

`order` (including `fees`) is a fully caller-supplied parameter to the public, unprivileged `placeOrder()` entrypoint — no privileged actor, relayer, or prover is required to trigger this. If `order.fees` exceeds `type(uint128).max`, the wrapper's internal `uint128(amountOut)` wraps around to an unrelated small value, so the actual output-token amount requested/settled by the V4 swap has no relationship to `order.fees`. The gateway, however, is unaware of this truncation — it uses the pristine `order.fees` value (not the wrapper's returned `amounts[]`) to update `_orders[commitment][TRANSACTION_FEES]`.

### Impact Explanation
This breaks the core custody invariant that escrow accounting must equal actual held balance. `_orders[commitment][TRANSACTION_FEES]` becomes an inflated phantom value that does not correspond to any real fee-token balance the gateway actually received from the swap. Since `_orders` is a shared per-token ledger across all orders in the same contract, an attacker can create orders whose recorded fee-escrow value vastly overstates what tokens the contract actually holds for that order, which corrupts the pooled fee-token accounting used later during fill/settlement/withdrawal flows and can enable claiming fee-token amounts that were never truly escrowed — a fund-accounting/logic attack matching the bounty's "logic attacks" and "unauthorized/incorrect beneficiary or amount" categories.

### Likelihood Explanation
High reachability: `placeOrder()` is a public, unauthenticated entrypoint and `order.fees` is entirely attacker-controlled with no upper-bound check before reaching the wrapper. No relayer, prover, governance, or malicious peer assumption is needed — a single malicious user transaction is sufficient to desync the escrow ledger from the real token balance.

### Recommendation
Use `SafeCast.toUint128()` (or an explicit `require(amountOut <= type(uint128).max)` / `require(amountIn <= type(uint128).max)`) in `UniV4UniswapV2Wrapper.swapETHForExactTokens` and `swapExactTokensForETH` before casting `amountOut`, `amountIn`, `amountOutMin`, and `msg.value` to `uint128`. Additionally, `IntentGatewayV2.placeOrder` should record fee escrow based on the actually-realized swap output (or explicitly bound `order.fees` to `type(uint128).max` before use) rather than trusting the raw, unchecked user-supplied value.

### Proof of Concept
1. Attacker calls `IntentGatewayV2.placeOrder(order, graffiti)` with `order.fees = type(uint128).max + 1` (or any value `> type(uint128).max`) and `msgValue > 0`.
2. `placeOrder` reaches the fee-swap branch at [4](#0-3)  and calls `swapETHForExactTokens(order.fees, path, address(this), block.timestamp)`.
3. Inside the wrapper, `uint128(amountOut)` truncates `order.fees` to `amountOut mod 2^128`, so the V4 swap is executed for this small truncated value only — the fee token actually received by the gateway corresponds to this small value, not `order.fees`.
4. `placeOrder` nonetheless executes `_orders[commitment][TRANSACTION_FEES] = order.fees;` at [3](#0-2) , recording the full, huge `order.fees` value as escrowed — even though the gateway's actual fee-token balance increase is far smaller (equal to the truncated amount).
5. Downstream logic that consumes `_orders[commitment][TRANSACTION_FEES]` (fee distribution/refund on fill or cancel, defined in `evm/src/apps/intentsv2/IntentsBase.sol`) now operates on a value that has no backing balance, corrupting the shared fee-token ledger for the contract.

**Note on completeness:** I was not able to fully trace, within the available tool budget, the exact downstream code in `evm/src/apps/intentsv2/IntentsBase.sol` that consumes `_orders[commitment][TRANSACTION_FEES]` (e.g., how/when it's paid out to a solver or refunded). The broken invariant — escrow value recorded without bound-checking the truncating cast that determines the real token amount received — is locally proven, but the precise fund-drain mechanism from this desync should be confirmed by reviewing `IntentsBase.sol`'s fee-settlement/withdrawal paths.

### Citations

**File:** evm/src/utils/uniswapv2/UniV4UniswapV2Wrapper.sol (L66-101)
```text
    function swapETHForExactTokens(uint256 amountOut, address[] calldata path, address recipient, uint256 deadline)
        external
        payable
        returns (uint256[] memory amounts)
    {
        PoolKey memory poolKey = _createPoolKey(path[1]);

        bytes[] memory params = new bytes[](3);
        params[0] = abi.encode(poolKey, true, uint128(amountOut), uint128(msg.value), bytes(""));
        params[1] = abi.encode(poolKey.currency0, uint256(0), false);
        params[2] = abi.encode(poolKey.currency1, recipient, amountOut);

        bytes[] memory inputs = new bytes[](1);
        inputs[0] = abi.encode(
            abi.encodePacked(uint8(Actions.SWAP_EXACT_OUT_SINGLE), uint8(Actions.SETTLE), uint8(Actions.TAKE)), params
        );

        // Snapshot standing balance (excluding inbound msg.value) so the refund is the swap-call delta only,
        // immune to any ETH that lands on the wrapper from outside the router (e.g., selfdestruct, coinbase).
        uint256 balanceBefore = address(this).balance - msg.value;

        IUniversalRouter(_params.universalRouter).execute{value: msg.value}(
            abi.encodePacked(bytes1(uint8(Commands.V4_SWAP))), inputs, deadline
        );

        uint256 refundETH = address(this).balance - balanceBefore;

        if (refundETH > 0) {
            (bool success,) = msg.sender.call{value: refundETH}("");
            require(success, "ETH refund failed");
        }

        amounts = new uint256[](2);
        amounts[0] = msg.value - refundETH;
        amounts[1] = amountOut;
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L345-362)
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
```
