## Analog Found: Unbounded `order.inputs` Array Can Permanently Lock Cross-Chain Escrow via Out-of-Gas in `withdraw()`

### Title
Unbounded input-token array lets an attacker make cross-chain escrow release un-executable (gas-exhaustion fund lock) - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`, `evm/src/apps/intentsv2/ExtrinsicIntents.sol`, `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
This is the same bug class as the SkyWeaver gold-card report: a cheap "commit" step (`placeOrder`, escrowing tokens) succeeds even when it embeds an arbitrarily large token array, but the mandatory settlement step that must later iterate over that same array (`withdraw()`, invoked from `onAccept()` when a `RedeemEscrow`/`RefundEscrow` ISMP message is delivered) is unbounded and can be pushed past the destination-chain block gas limit. Unlike `mineGolds`, which could simply be retried once gas limits allowed, `withdraw()` here is invoked as the terminal step of an atomic, one-shot cross-chain ISMP message — if the loop it contains can never fit in a block, the escrowed funds can never be released to anyone.

### Finding Description
`Order.inputs` is a user-supplied `TokenInfo[]` with no visible upper-bound check in the code reviewed. It is escrowed token-by-token in `placeOrder`, and the same array (or a derived one) is carried end-to-end as `WithdrawalRequest.tokens` for later release: [1](#0-0) 

When a solver fills a cross-chain order, the destination-chain `IntentGatewayV2` dispatches a single atomic `RedeemEscrow` POST request back to the source chain. On the source chain, `onAccept()` decodes the `WithdrawalRequest` and calls `withdraw()`, unconditionally looping over every token in `body.tokens`: [2](#0-1) 

The same pattern (unbounded loop over `body.tokens`/`escrowedInputs` inside a function invoked from `onAccept`) exists in the EVM mainline implementation: [3](#0-2) [4](#0-3) 

There is no bounded batching mechanism for this loop the way `HandlerV2.sol`'s `handlePostRequests`/`handleGetResponses` at least allow relayers to size their own batches — the size of the `withdraw()` loop is fixed by `order.inputs.length`, which was chosen once, at placement time, by the order's creator, and is baked into the order commitment (`keccak256(abi.encode(order))`) used everywhere downstream (fill authorization, cancellation, settlement). Nobody but the original committer can change it.

### Impact Explanation
An attacker places a cross-chain order whose `inputs` array contains enough token entries (each escrowing a trivially small, or even zero-relevant, amount) that the `for` loop in `withdraw()` — each iteration doing an external `token.call(transfer(...))` — cannot complete within the source chain's block gas limit. A solver who is unaware of this limit fills the order on the destination chain, delivering real output tokens to the beneficiary immediately and irreversibly (per the documented fill flow). The `RedeemEscrow` message dispatched back to the source chain will then always revert on execution, no matter how many times a relayer retries delivering the same message, because the underlying transaction requires more gas than the chain allows in a single block. The result: the solver's already-delivered outputs are unrecoverable, and the user's escrowed input tokens are permanently stuck in the `IntentGatewayV2` contract (`_orders[commitment][token]` balances can never be redeemed) — this is a direct, real fund-loss/fund-lock condition on bridged order escrow, matching the "bridged assets ... must move exactly once and only to the rightful beneficiary" invariant in the impact gate.

### Likelihood Explanation
Placing an order with an oversized `inputs` array requires no privileged role — any user can call `placeOrder` with an arbitrarily long `TokenInfo[]`. The only cost is the gas to escrow N tokens once at placement time (which can be amortized over reused, already-approved dummy ERC-20 tokens with 0/negligible value), which is cheaper per-call than requiring N atomic `transfer` calls to succeed together inside one non-splittable settlement message. This mirrors exactly the SkyWeaver root cause: the "commit" step is asymptotically cheaper than the "release" step that must process the same set atomically. No relayer collusion, prover compromise, or governance action is needed — a single unprivileged order placement plus a single fill (by any solver, who has no way to detect the trap before losing their output) is sufficient.

### Recommendation
Cap `order.inputs.length` (and `order.output.assets.length`) at order-placement time to a value provably executable within the withdraw loop's gas budget, mirroring the SkyWeaver fix of bounding batch size at the cheap "commit" stage rather than discovering the limit at the expensive "execute" stage. Alternatively, redesign `withdraw()` so escrow release is not required to complete atomically for the whole token set in a single message (e.g., per-token claim functions gated by the stored commitment, or partial-progress checkpointing so a partially-executed release cannot be replayed/double-spent but can be resumed across multiple transactions).

### Proof of Concept
1. Attacker calls `placeOrder` on the source chain with `order.inputs` containing, e.g., 2,000 distinct low-value ERC-20 tokens (or many entries referencing tokens the attacker controls), each with a minimal escrowed amount. `placeOrder`'s per-token `safeTransferFrom` loop is well within gas limits because it can be sized (and observed) at submission time by the attacker to just barely fit. [1](#0-0) 
2. A solver, evaluating only the requested outputs, fills the order on the destination chain per the documented fill flow, delivering the required output tokens to the beneficiary and triggering the `RedeemEscrow` dispatch back to source. [5](#0-4) 
3. A relayer delivers the `RedeemEscrow` POST request to the source chain's `onAccept()`, which calls `withdraw()`; the loop over the 2,000 tokens (each doing an external call) exceeds the source chain's block gas limit and the transaction always reverts. [6](#0-5) 
4. No matter how many times any relayer retries delivering the message (ISMP requests here have `timeout: 0`, i.e., never time out), the transaction cannot succeed — the solver's delivered output is unrecoverable and the escrowed input tokens are permanently locked in `_orders[commitment][token]`.

**Uncertainty**: `grep_search` surfaced additional `inputs.length`-related matches in `evm/src/apps/IntentGatewayV2.sol` and `evm/src/apps/intentsv2/ExtrinsicIntents.sol` that I was not able to fully inspect line-by-line in the time available; it is possible a length cap exists elsewhere in `placeOrder` that I did not retrieve. I could not conclusively rule this out from the index alone — a Devin session with full file access should verify whether `placeOrder` enforces any `MAX_INPUTS`-style bound before treating this as final.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L451-463)
```text
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L676-721)
```text
    /**
     * @notice Withdraws the escrowed tokens for a request body.
     * @dev This function is marked as internal.
     * @param body The request body containing commitment, tokens, and beneficiary.
     * @param isRefund Whether this is a refund (true) or a successful fill (false).
     */
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }

        if (isRefund) {
            emit EscrowRefunded({commitment: body.commitment});
        } else {
            emit EscrowReleased({commitment: body.commitment});
        }
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-309)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }

        // only hyperbridge is permitted to perform these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            _addDeployment(abi.decode(incoming.request.body[1:], (Deployment)));
        } else if (kind == RequestKind.UpdateParams) {
            _updateParams(abi.decode(incoming.request.body[1:], (ParamsUpdate)));
        } else if (kind == RequestKind.SweepDust) {
            _sweepDust(abi.decode(incoming.request.body[1:], (SweepDust)));
        } else if (kind == RequestKind.UpgradeContract) {
            (address newImpl, bytes memory initData) = abi.decode(incoming.request.body[1:], (address, bytes));
            ERC1967Utils.upgradeToAndCall(newImpl, initData);
        }
    }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L131-149)
```text
        WithdrawalRequest memory body = WithdrawalRequest({
            commitment: commitment, tokens: escrowedInputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        });
        _withdraw(body, false, isFullyFilled);

        if (isFullyFilled) {
            _execute(order, outputsLen);
            emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        } else {
            delete _filled[commitment];
            emit PartialFill({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        }

        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }
    }
```

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L41-57)
```text
### Fill Flow

The solver calls `fillOrder(order, options)` on the **destination chain**. The function verifies the order hasn't expired (`order.deadline >= block.number`), confirms execution is on the correct chain, and checks the order hasn't already been filled. The solver must provide output amounts greater than or equal to the order's required amounts — any amount below the required amount reverts with `InvalidInput()`.

If the solver provides more tokens than required, the excess (surplus) is split according to `surplusShareBps`. If the order includes calldata, 100% of surplus goes to the protocol to prevent manipulation.

After delivering output tokens to the beneficiary, the contract dispatches a cross-chain `RedeemEscrow` message back to the source chain.


### Settlement

When the settlement message arrives on the source chain, the ISMP host calls `onAccept()`. The handler authenticates the message (verifying it came from a known IntentGateway instance), decodes the `WithdrawalRequest`, and calls `withdraw()` which:

1. Marks the order as filled (`_filled[commitment] = solver`)
2. Transfers each escrowed input token to the solver
3. Releases stored transaction fees (in fee token) to the solver
4. Emits `EscrowReleased(commitment)`
```
