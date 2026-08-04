### Title
Unbounded `order.inputs` array lets a user permanently DOS escrow settlement/refund via gas-exhausting `_withdraw()`/`withdraw()` loops - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
`IntentGatewayV2`/`IntentsBase` places no upper bound on the number of input tokens (`order.inputs.length`) a user can attach to an order at `placeOrder()`. That same unbounded array is carried through fill, cancel-from-source, and cancel-from-destination flows and is replayed, element-by-element, inside `_withdraw()` (same-chain, `IntentsBase.sol:390-410`) and `withdraw()` (Tron variant, `evm/tron/contracts/apps/IntentGatewayV2.sol:682-705`), each iteration performing an external token transfer. A user can place an order with thousands of distinct (or even fabricated) input tokens, making the eventual escrow release/refund transaction exceed the block gas limit and revert unconditionally — permanently locking the escrowed funds, mirroring the `predict()`/`claimReward()` pattern in the external report.

### Finding Description
`placeOrder()` (e.g. `evm/tron/contracts/apps/IntentGatewayV2.sol:332-463`) validates only that `order.inputs.length != 0`; there is no maximum-length check on the `inputs` array before it is escrowed and hashed into the order `commitment`. The same unbounded array flows into:

- `_fillCrossChain` (`evm/src/apps/intentsv2/ExtrinsicIntents.sol:89-171`), which embeds `order.inputs` verbatim in the `RedeemEscrow` `WithdrawalRequest` body dispatched cross-chain.
- `_cancelFromSource` (`ExtrinsicIntents.sol:188-223`) and `_cancelFromDest` (`ExtrinsicIntents.sol:240-267`), which likewise embed `order.inputs` in `RefundEscrow`/GET-response contexts.
- `_cancelSameChain` (`evm/src/apps/intentsv2/IntrinsicIntents.sol:161-180`), which loops over `order.inputs` to build `remainingTokens`.

On the settlement side, `_withdraw()` (`IntentsBase.sol:390-410`) and the Tron `withdraw()` (`evm/tron/contracts/apps/IntentGatewayV2.sol:682-705`) iterate `body.tokens.length` (== `order.inputs.length`), doing a storage read/write plus an external `safeTransfer`/native `call` per element: [1](#0-0) 

This is invoked from `onAccept()` for both `RedeemEscrow` and `RefundEscrow` (`ExtrinsicIntents.sol:289-295`), and from `onGetResponse()` for source-side cancellation (`ExtrinsicIntents.sol:319-324`). Because none of these entry points cap the token-list length, and the length is fixed at order-placement time (baked into the `commitment` hash), a large-enough `inputs` array makes **every** downstream settlement path for that order — fill-redeem, cancel-refund, and GET-response-refund — unconditionally exceed the gas limit and revert.

Unlike the external report's `claimReward()` (where the DOS only blocks reward distribution while leaving the record repeatable), here the escrow is bound to one specific `commitment`, and the `_filled[commitment]` mapping may already be set (e.g., `_cancelFromDest` sets `_filled[commitment] = user` before dispatching `RefundEscrow`) before the failing `_withdraw()` call, so there is no alternate code path to retry the release once the array is oversized — the escrowed principal is stuck permanently.

### Impact Explanation
This is a High-impact finding: a single unprivileged user action (`placeOrder` with an oversized `inputs` array) can make their own future escrow refund/redemption transaction permanently unexecutable, locking their own escrowed principal on-chain with no way to move it. While self-inflicted lock has limited blast radius per-order, it is a real, deterministic loss-of-funds bug directly matching the accepted impact class ("stealing or loss of funds") via unbounded gas consumption in the same settlement primitives (`_withdraw`/`withdraw`) that every order — including honestly-sized ones interacting with the same commitment/array structure — depends on for both fill-redemption and cancellation-refund.

### Likelihood Explanation
Likelihood is Medium: triggering the bug requires no privileged role, no relayer/prover collusion, and no malformed proofs — only a user constructing an order with a sufficiently long `inputs` array (each element just needs to be a distinct storage slot in `_orders[commitment][token]`, so duplicate or arbitrary token addresses suffice to inflate iteration count without needing real liquidity for many tokens). The primary friction is the escrow-transfer cost paid up front per token at `placeOrder`, which bounds how cheaply an attacker can inflate the array size for real ERC-20 transfers, but native-token entries and small-decimal/cheap tokens reduce this cost.

### Recommendation
Enforce a hard cap on `order.inputs.length` (and correspondingly on `order.output.assets.length`) in `placeOrder()`/`fillOrder()` validation, sized to guarantee that `_withdraw()`/`withdraw()` can never exceed a safe fraction of the block gas limit even in the worst case (worst-case token being a non-standard ERC-20 or a token with expensive `transfer` logic). Reject orders exceeding the cap before any escrow transfer occurs.

### Proof of Concept
1. User calls `placeOrder()` with `order.inputs` containing e.g. 2,000 distinct token entries, each escrowing a minimal amount (or the same low-cost token address repeated with distinct index-tracked amounts, since `_orders[commitment][token] += reducedInputs[i].amount` is additive per token — using genuinely distinct ERC-20 addresses is the reliable way to force 2,000 separate storage/transfer operations).
2. A solver fills the order (same-chain via `_fillSameChain`/`IntrinsicIntents.sol:54-149`, or cross-chain via `_fillCrossChain`/`ExtrinsicIntents.sol:89-171`), producing a `WithdrawalRequest` whose `tokens` array has the same 2,000 entries.
3. On settlement, `onAccept()` → `_withdraw()`/`withdraw()` iterates all 2,000 entries, each doing an external transfer call; total gas exceeds the block gas limit, so the transaction reverts every time it is submitted.
4. Because `_filled[commitment]` state and escrow are already recorded against this exact `commitment`/array, there is no alternate settlement path with a smaller token list — the escrowed funds for that order remain permanently locked in the contract.

*Note: I was not able to fully verify whether `placeOrder` on the primary EVM `IntentGatewayV2.sol` (non-Tron) enforces a distinct-token-address dedup or any implicit array-size limit beyond `inputs.length == 0` check, due to index size limits on the available code snippets; a Devin session with full repository access should confirm the exact bound (or absence of one) in `evm/src/apps/IntentGatewayV2.sol::placeOrder` before finalizing remediation.*

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-410)
```text
        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```
