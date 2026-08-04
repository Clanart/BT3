## Analysis

Reducing the external report to its core invariant: **an internal ledger balance was decremented after the external value transfer, so a reentrant/duplicate call sees the stale (pre-decrement) balance and can be paid a second time.**

Searching Hyperbridge for the same class of bug, the strongest local analog is the internal `withdraw()` function in `IntentGatewayV2`, which pays out escrowed order funds (both same-chain redemptions and cross-chain refunds).

### The corrupted value
In `withdraw()`, for each token in the withdrawal request, the code does:
1. `require(_orders[commitment][token] != 0)`
2. an **external call** transferring the ETH/ERC-20 to the beneficiary
3. **only afterward** decrements `_orders[commitment][token] -= amount` [1](#0-0) 

The same fee-transfer-then-delete ordering repeats for the escrowed transaction fee slot at the end of the function (external `feeToken.call(transfer)` before `delete _orders[commitment][TRANSACTION_FEES]`) — an identical external-call-before-state-update pattern to `RaffleMintV1.withdrawNonRaffleProceeds()`.

Crucially, unlike `fillOrder`'s `_fillSameChain`/`_fillCrossChain` paths — which were explicitly hardened with a CEI fix so `_filled[commitment]` is set *before* any external call and guarded with `revert Filled()` on re-entry (documented and tested in `IntrinsicIntentsReentrancyTest.sol`) — `withdraw()` itself contains **no equivalent guard**. It unconditionally does `_filled[body.commitment] = beneficiary;` at the top with no `if (_filled[commitment] != address(0)) revert` check, and the only anti-double-spend control is the per-token `_orders[commitment][token] == 0` check, which is exactly the value left stale during the external call. [2](#0-1) 

The beneficiary of this payout is attacker-controlled: for the `RefundEscrow`/cancellation path, `body.beneficiary = order.user`, and `order.user` is set by whoever placed the order. [3](#0-2) 

`withdraw()` is only invoked from `onAccept` (for `RedeemEscrow`/`RefundEscrow`, gated `onlyHost`) and `onGetResponse` (also `onlyHost`): [4](#0-3) [5](#0-4) 

## What I could not verify

I could not confirm, within the remaining tool budget, whether the EVM host's message-batch processing loop (the code that calls `onAccept`/`onGetResponse`) could deliver two messages for the same commitment within a single external call context (e.g., a duplicate `RedeemEscrow` and `RefundEscrow` racing through the same batch, or a retried delivery after a partial revert as seen in `EvmHost.sol`'s `dispatchIncoming`/`dispatchTimeOut` retry-on-failure pattern). That retry-on-failure pattern is itself relevant: [6](#0-5) 

If a message can be redelivered/replayed for the same commitment before the first `withdraw()` invocation's per-token decrements complete (e.g., through the documented "so that it can be retried" receipt-deletion-on-failure branches, combined with a griefing revert crafted by the malicious beneficiary on one output leg), the stale `_orders[commitment][token]` values remain nonzero and a second `withdraw()` pass would pay out the same escrow again. I was not able to fully trace the host's outer dispatch loop to confirm reachability of this double-invocation without an unprivileged/malicious-relayer assumption (which the task explicitly excludes), so I present this as the strongest **locally grounded but partially unverified** analog rather than a fully proven end-to-end exploit.

### Title
Missing double-invocation guard and post-call state update in `IntentGatewayV2.withdraw()` — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`withdraw()`, which pays out escrowed order funds on redeem/refund, transfers value to an attacker-controlled beneficiary before decrementing the `_orders[commitment][token]` ledger, and does not guard against being invoked twice for the same commitment the way `fillOrder`'s sibling paths do.

### Finding Description
`withdraw()` sets `_filled[commitment] = beneficiary` unconditionally (no revert-if-already-filled check, unlike the hardened `_fillSameChain`/`_fillCrossChain` paths), then for each escrowed token performs `beneficiary.call{value: amount}("")` or an ERC-20 `transfer` *before* reducing `_orders[commitment][token]`, and does the same for the escrowed transaction fee. This is the checks-call-effects ordering explicitly flagged as vulnerable in the external report.

### Impact Explanation
If `withdraw()` is invoked a second time for the same commitment before the first invocation's decrements land (via retried/duplicate delivery of the corresponding `RedeemEscrow`/`RefundEscrow` message, a scenario the host's own "delete on failure so it can be retried" pattern makes structurally possible), the escrow for that order is paid out twice to the attacker-controlled beneficiary — direct loss of bridged/escrowed funds, matching the bounty's "double-claim/double-settlement" and "stealing or loss of funds" categories.

### Likelihood Explanation
Low-to-moderate and not fully confirmed: the entry points into `withdraw()` are `onlyHost`-gated, so a bare reentrant external call from the malicious beneficiary cannot call them directly. Exploitability depends on whether the host's message batching/retry logic can redeliver the same commitment's message within an exploitable window — a path I could not fully trace in this session.

### Recommendation
Apply the same CEI discipline already used for `fillOrder`: add an explicit idempotency check (e.g. revert if this commitment was already withdrawn) at the top of `withdraw()`, and reorder each token/fee handling to update `_orders[commitment][token]` (and delete the fee entry) before making the external transfer call.

### Proof of Concept
Not independently reproduced; contingent on confirming the host can redeliver/duplicate a `RedeemEscrow`/`RefundEscrow` message for the same commitment (see "What I could not verify" above). Recommend a Devin session with full repo/tool access to trace `EvmHost`'s inbound batch-processing loop and construct a concrete Foundry PoC analogous to `IntrinsicIntentsReentrancyTest.sol`.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L586-591)
```text
            _filled[commitment] = address(uint160(uint256(order.user)));

            bytes memory body = bytes.concat(
                bytes1(uint8(RequestKind.RefundEscrow)),
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
            );
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-721)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
```

**File:** evm/src/core/EvmHost.sol (L811-818)
```text

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
    }
```
