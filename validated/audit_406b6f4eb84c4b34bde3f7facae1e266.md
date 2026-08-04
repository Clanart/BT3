## Title
Sandwichable exact-output fee swap in `placeOrder` locks user's excess native token with no refund path — (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, `placeOrder()` swaps native token for the exact `order.fees` amount of `feeToken` via `IUniswapV2Router02.swapETHForExactTokens{value: msgValue}(order.fees, path, address(this), block.timestamp)` [1](#0-0) . Unlike the mainline EVM contract at `evm/src/apps/IntentGatewayV2.sol`, which after the identical swap deducts the router's actual spend (`msgValue -= amounts[0]`) and then refunds any unspent native token back to `msg.sender` [2](#0-1) , the Tron variant discards the router's returned `amounts[0]` entirely and has no unspent-native-token refund block after the fee logic [3](#0-2) .

### Finding Description
`swapETHForExactTokens` is an exact-output swap: it only consumes as much of the supplied `msg.value` as the pool price requires to produce exactly `order.fees` tokens, and the UniswapV2 router itself refunds any unused ETH — but that refund goes to `msg.sender` of the router call, which is the `IntentGatewayV2` contract itself, not the end user [4](#0-3) .

The core broken invariant mirrors the external report's pattern (an embedded, unprotected AMM swap during a state-mutating entrypoint whose settlement price is attacker-influenceable): here, the price used to convert native token into `feeToken` is whatever the pool quotes at execution time, with no `amountInMax` cap distinct from the entire `msgValue`, and — critically — the leftover ETH that the router refunds to the contract is never accounted for or returned to the user in this file. Any native token that the user attached beyond what the (potentially sandwich-inflated) swap consumed becomes stranded contract balance rather than being credited back.

An attacker can front-run a `placeOrder` call that carries `order.fees > 0` and pays with native token, manipulating the WETH/feeToken pool price upward so the swap consumes more (or less) ETH than the fair-market rate would require. Because the contract's `msgValue` local variable is never decremented by the actual `amounts[0]` spent, and no refund path exists, whatever the router returns as unspent ETH sits in the `IntentGatewayV2` contract balance — it is not credited to the placing user's account, not part of any escrow accounting (`_orders[commitment][...]`), and not swept as protocol dust either (dust accounting only covers ERC-20/predispatch flows via `DustCollected`, not this fee-swap native leftover). This is a genuine unauthorized-loss/fund-lock path reachable by any unprivileged user who places a fee-in-native order, and is exacerbated (though not solely caused) by sandwich attacks that widen the gap between `msgValue` supplied and the true amount required.

### Impact Explanation
This falls under the bounty's "stealing or loss of funds" category. Users placing orders with `order.fees > 0` and paying via native token on the Tron deployment permanently lose any native token sent beyond the actual swap cost — funds become stuck in the contract with no accounted mechanism to recover them (they are not tracked in `_orders`, not part of the documented dust-sweep flow for protocol governance, and not refunded to the user). A sandwich attack on the WETH→feeToken pool amplifies the loss per transaction by forcing the swap to consume close to the entire `msgValue`, but even without any adversary, ordinary users who slightly overestimate `msg.value` lose the difference.

### Likelihood Explanation
Every same-chain or cross-chain order placed through this Tron contract with a non-zero `order.fees` paid in native token exercises this path unconditionally — no special conditions, privileged roles, or malicious relayer/prover involvement are required. The trigger is a completely standard, publicly documented SDK usage pattern (native fee payment), matching the "Payment Methods" flow described for `DispatchPost` fees [5](#0-4) . The only variable is the magnitude of loss, which sandwich manipulation of the AMM pool increases but does not create.

### Recommendation
Mirror the mainline `evm/src/apps/IntentGatewayV2.sol` fix in the Tron contract: capture the router's returned `amounts[0]`, decrement `msgValue` by that amount, and refund any remaining `msgValue` to `msg.sender` after the fee-swap block, exactly as done at [6](#0-5) .

### Proof of Concept
1. User calls `placeOrder{value: X}(order, graffiti)` on the Tron `IntentGatewayV2` where `order.fees = F > 0` and `X` comfortably covers `F` plus a safety margin (standard SDK behavior, since the exact router-required input is not knowable in advance).
2. Inside `placeOrder`, `swapETHForExactTokens{value: msgValue}(F, path, address(this), block.timestamp)` executes; suppose it only needs `amounts[0] = A < X` ETH to produce `F` fee tokens. The router refunds `X - A` ETH back to `address(this)` (the gateway contract).
3. The Tron contract never reads `amounts[0]`, never decrements `msgValue`, and has no subsequent refund-to-`msg.sender` step in this code path (compare lines 465-497 with the presence of such logic in `evm/src/apps/IntentGatewayV2.sol` lines 356-368).
4. The `X - A` ETH remains in the `IntentGatewayV2` contract's balance, uncredited to any escrow or dust-sweep accounting, and is not returned to the user who sent it — a direct, unauthorized loss of user funds. [7](#0-6) [2](#0-1)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L465-497)
```text
        if (order.fees > 0) {
            // escrow fees
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }

        emit OrderPlaced({
            user: order.user,
            source: order.source,
            destination: order.destination,
            deadline: order.deadline,
            nonce: order.nonce,
            fees: order.fees,
            session: order.session,
            predispatch: order.predispatch.assets,
            inputs: reducedInputs,
            beneficiary: order.output.beneficiary,
            outputs: order.output.assets
        });
    }
```

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
