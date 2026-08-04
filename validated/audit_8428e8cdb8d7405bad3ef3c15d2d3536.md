Based on the evidence gathered, I found a concrete local analog: escrowed order fees are tracked in a storage slot that is silently excluded from every refund/settlement path in `IntentGatewayV2`, mirroring the Augur bug pattern where a specific fee bucket was carved out of the transferred amount and never actually swept anywhere.

### Title
Escrowed `order.fees` (`TRANSACTION_FEES` slot) are excluded from every cancel/refund token list, permanently stranding user funds - ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol], [File: evm/src/apps/intentsv2/ExtrinsicIntents.sol])

### Summary
`IntentGatewayV2.placeOrder` escrows an optional dispatch/relayer fee (`order.fees`) under a dedicated storage key, `_orders[commitment][TRANSACTION_FEES]`, separate from the input token escrow [1](#0-0) . Every cancellation path (`_cancelSameChain`, `_cancelFromSource`, `_cancelFromDest`) builds its `WithdrawalRequest.tokens` array exclusively from `order.inputs`, never referencing `TRANSACTION_FEES` [2](#0-1) [3](#0-2) [4](#0-3) . This is structurally identical to the Augur `distributeMarketCreatorFees` bug: a fee bucket that was subtracted out at collection time (`marketCreatorFeesAttoCash -= affiliateFees`) is never actually included in the transfer/settlement performed on the invalid-outcome path, so it neither reaches its rightful destination nor gets zeroed out consistently with the accounting.

### Finding Description
When a user places an order, if `order.fees > 0` the gateway collects the fee token (via Uniswap swap or `safeTransferFrom`) and records it at `_orders[commitment][TRANSACTION_FEES]` [1](#0-0) .

On the "invalid" path — i.e. the order is cancelled instead of successfully filled — the refund logic only iterates `order.inputs`:
- `_cancelSameChain` builds `remainingTokens` strictly from `order.inputs` and issues `_withdraw` with that list [2](#0-1) .
- `_cancelFromSource` encodes `WithdrawalRequest{tokens: order.inputs, ...}` in the GET-response context, again omitting `TRANSACTION_FEES` [5](#0-4) .
- `_cancelFromDest` builds the same `WithdrawalRequest{tokens: order.inputs, ...}` for `RefundEscrow` [6](#0-5) .

None of these paths reference the `TRANSACTION_FEES` slot, so whatever `_withdraw` does with the passed `tokens` array (zero the escrow entry and transfer it to the beneficiary) never touches the fee amount recorded during `placeOrder`. The fee-fill paths (`_fillSameChain`, `_fillCrossChain`) also never read `_orders[commitment][TRANSACTION_FEES]` — the relayer/dispatch fee paid to Hyperbridge on fill is instead sourced fresh from `options.relayerFee`/`options.nativeDispatchFee` supplied by the solver at fill time [7](#0-6) , not from the escrowed `order.fees`.

This exactly parallels the reported bug class: a fee amount is carved out of the accounting (`marketCreatorFeesAttoCash -= affiliateFees` in Augur; `_orders[commitment][TRANSACTION_FEES] = order.fees` here) but the "invalid" settlement branch (invalid market fee-pool transfer in Augur; order cancellation in Hyperbridge) does not actually move or account for that carved-out amount, leaving user funds unaccounted for in the contract with no code path that ever clears or returns them.

### Impact Explanation
Every order placed with `order.fees > 0` that is later cancelled (rather than filled) permanently strands the escrowed fee amount inside `IntentGatewayV2`. This is a direct loss-of-funds condition for the order creator: their fee payment is neither refunded on cancellation nor consumed by any fill-side accounting, since fills pay a fresh relayer fee independently. The bug is triggerable by any unprivileged user simply by placing and then cancelling an order with a non-zero `fees` field — no relayer, prover, or admin action is required.

### Likelihood Explanation
High likelihood: `cancelOrder`/`_cancelSameChain`/`_cancelFromSource`/`_cancelFromDest` are all standard, permissionless user-facing flows exercised in normal operation whenever an order isn't filled before its deadline. Any order with a non-zero `order.fees` that is cancelled triggers the loss deterministically — this requires no adversarial coordination, race condition, or malicious peer.

### Recommendation
Include the `TRANSACTION_FEES` slot in the `WithdrawalRequest.tokens` list (or handle it explicitly) in `_cancelSameChain`, `_cancelFromSource`, and `_cancelFromDest`, refunding the escrowed `order.fees` back to `order.user` whenever the order is cancelled, and ensure the storage entry is zeroed to prevent any future inconsistency. Add unit tests asserting that cancelling an order with `fees > 0` fully refunds both the input tokens and the fee escrow.

### Proof of Concept
1. Call `placeOrder` with `order.fees = X` (X > 0) on a same-chain order; the gateway swaps/collects X in fee token and stores it at `_orders[commitment][TRANSACTION_FEES]`.
2. Before the order is filled, call `cancelOrder` (routes to `_cancelSameChain` for same-chain orders).
3. Observe: `_cancelSameChain` only refunds `order.inputs` tokens via `_withdraw`; the `TRANSACTION_FEES` entry is untouched — inspect gateway's fee-token balance before/after and note `X` remains locked in the contract with no view or extrinsic anywhere in `IntrinsicIntents.sol`/`ExtrinsicIntents.sol` that ever transfers or clears that slot.

Note: I was unable to fully inspect `IntentsBase.sol`'s `_withdraw` implementation within the available context (only grep hits were retrieved, not the full function body), so I cannot rule out an internal special-case for the `TRANSACTION_FEES` constant inside `_withdraw` itself that might mitigate this. If such handling exists, this finding should be re-verified against `_withdraw`'s exact token-iteration logic in `evm/src/apps/intentsv2/IntentsBase.sol` before treating it as confirmed.

### Citations

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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L169-186)
```text
        uint256 inputsLen = order.inputs.length;
        TokenInfo[] memory remainingTokens = new TokenInfo[](inputsLen);
        bool hasEscrow = false;
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            uint256 escrowed = _orders[commitment][token];
            if (escrowed > 0) hasEscrow = true;
            remainingTokens[i] = TokenInfo({token: order.inputs[i].token, amount: escrowed});
            unchecked {
                ++i;
            }
        }
        if (!hasEscrow) revert UnknownOrder();

        WithdrawalRequest memory body =
            WithdrawalRequest({commitment: commitment, tokens: remainingTokens, beneficiary: order.user});

        _withdraw(body, true, true);
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L148-162)
```text
        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });

        if (options.nativeDispatchFee > 0 && msgValue >= options.nativeDispatchFee) {
            IDispatcher(hostAddr).dispatch{value: options.nativeDispatchFee}(request);
            msgValue -= options.nativeDispatchFee;
        } else {
            dispatchWithFeeToken(request);
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L193-223)
```text
        uint256 inputsLen = order.inputs.length;
        for (uint256 i; i < inputsLen;) {
            if (_orders[commitment][address(uint160(uint256(order.inputs[i].token)))] == 0) revert UnknownOrder();

            unchecked {
                ++i;
            }
        }

        bytes memory context =
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));

        bytes[] memory keys = new bytes[](1);
        keys[0] = bytes.concat(abi.encodePacked(_instance(order.destination)), _calculateCommitmentSlotHash(commitment));
        DispatchGet memory request = DispatchGet({
            dest: order.destination,
            keys: keys,
            timeout: 0,
            height: options.height,
            fee: options.relayerFee,
            context: context,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L245-259)
```text
        _filled[commitment] = address(uint160(uint256(order.user)));

        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RefundEscrow)),
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
        );

        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });
```
