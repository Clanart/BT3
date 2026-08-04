## Analysis

The external report's core broken invariant: **a mandatory outbound token transfer to a `beneficiary`/`borrower` address is used as a blocking precondition for finalizing settlement, and if that transfer permanently reverts (e.g. blacklist), the settlement step can never complete, permanently locking the escrowed/pooled funds.**

Hyperbridge's `IntentGatewayV2` intent-settlement system has the exact same broken invariant.

### Title
Blacklisted order beneficiary permanently locks escrowed intent funds with no recovery path - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentGatewayV2`'s cross-chain escrow settlement (`RedeemEscrow`/`RefundEscrow`) unconditionally pushes tokens to a `beneficiary` address via `safeTransfer`/raw `transfer` calls inside `_withdraw()`. If that beneficiary is later blacklisted by the escrowed token (e.g. USDC/USDT), the transfer reverts, the whole settlement message fails, and the host's retry mechanism guarantees the failure repeats forever — permanently trapping the escrowed input tokens in the gateway contract.

### Finding Description
When a cross-chain order is filled or cancelled, the destination gateway dispatches a `RedeemEscrow`/`RefundEscrow` POST request back to the source chain. On the source chain, `EvmHost.dispatchIncoming` calls the app's `onAccept`: [1](#0-0) 

Note the retry design: if the low-level `.call` to `onAccept` fails, the host deletes the request receipt "so it can be retried" and returns — it does **not** discard the message. This is meant to tolerate transient failures (e.g. out-of-gas), but it also means a *permanently* failing call (blacklist) can be resubmitted indefinitely and will fail identically every time.

`onAccept` in the intent gateway routes `RedeemEscrow`/`RefundEscrow` to `_withdraw()`: [2](#0-1) 

`_withdraw` decrements the escrow accounting and then unconditionally transfers to `beneficiary` via `IERC20(token).safeTransfer` (or, in the Tron/EVM legacy variant, an explicit low-level `transfer` call that reverts with `TransferFailed()` on failure): [3](#0-2) 

There is no try/catch, no fallback recipient, and no `claims[token]`-style pull mechanism (exactly the mitigation recommended — and only partially implemented — in the original report). Because `_withdraw` reverts the entire `onAccept` call on transfer failure, and `EvmHost.dispatchIncoming` treats any `onAccept` failure as "retry later," a beneficiary that becomes blacklisted for the escrowed token (this can happen to either the order's `user` on cancellation/refund, or the `solver` on redemption) makes this specific order's escrow permanently unsettleable. The same-chain cancellation and cross-chain `onGetResponse` path (`withdraw(body, true)`) has the identical unconditional-transfer pattern with no fallback.

### Impact Explanation
The escrowed input tokens (and any accrued `TRANSACTION_FEES`) for that specific order become permanently locked inside the `IntentGatewayV2` contract — neither the user, nor the intended solver, nor governance has a code path to recover them, since `_orders[commitment][token]` can only be zeroed by a successful `_withdraw` call that will keep reverting. This is a direct, protocol-level fund-lock matching the bounty's "stealing or loss of funds" / "logic attacks" impact category, and it requires no malicious relayer, prover, or admin — only an ordinary compliance event (blacklist) affecting a normal, non-malicious counterparty.

### Likelihood Explanation
Token issuer blacklisting (USDC, USDT, and similar centralized stablecoins used as intent-gateway assets) is a real, non-hypothetical, non-attacker-controlled event. Any order whose `user` or `solver` beneficiary gets blacklisted between order placement and settlement finalization triggers this path automatically, with no attacker action required — matching the same "rare but real, no recovery" characterization the sponsor themselves acknowledged in the original report, but without the partial mitigations (liquidator-controlled swap amount) that existed in the original Particle protocol.

### Recommendation
Adopt the pull-based `claims[token][beneficiary]` pattern suggested in the original report: wrap the `safeTransfer`/raw transfer in a try/catch (or low-level call whose failure is checked without reverting the whole settlement), and on failure credit an internal claims balance instead of reverting `_withdraw`/`onAccept`. Provide a separate permissionless `claim(token)` function that lets the beneficiary redirect the credited amount to an alternate address they control. This preserves escrow-accounting finality (the order is marked settled) while decoupling it from the beneficiary's ability to directly receive the specific token.

### Proof of Concept
1. User places a cross-chain order on chain A escrowing USDC as `order.inputs`, with destination chain B.
2. Solver fills the order on chain B and the gateway dispatches a `RedeemEscrow` POST back to chain A with `beneficiary = solver`.
3. Before the message is relayed/settled, Circle blacklists the solver's address for USDC (or the solver was already blacklisted for an unrelated reason).
4. Relayer delivers the `RedeemEscrow` message to chain A's `EvmHost.dispatchIncoming` → `IntentGatewayV2.onAccept` → `IntentsBase._withdraw` → `IERC20(USDC).safeTransfer(solver, amount)` reverts.
5. `dispatchIncoming` catches the failure, deletes `_requestReceipts[commitment]`, and returns without reverting the batch — allowing indefinite resubmission.
6. Every resubmission of the same message reverts identically forever; `_orders[commitment][USDC]` is never decremented, and the escrowed USDC is permanently stuck in the `IntentGatewayV2` contract on chain A with no available recovery function.

### Citations

**File:** evm/src/core/EvmHost.sol (L794-818)
```text
    function dispatchIncoming(PostRequest memory request, address relayer) external restrict(_hostParams.handler) {
        address destination = _bytesToAddress(request.to);
        uint256 size;
        assembly {
            size := extcodesize(destination)
        }
        if (size == 0) {
            // instead of reverting the entire batch, early return here.
            return;
        }

        // replay protection
        bytes32 commitment = request.hash();
        _requestReceipts[commitment] = relayer;

        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-425)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

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

        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }

            if (isRefund) {
                emit EscrowRefunded({commitment: body.commitment, tokens: body.tokens});
            } else {
                emit EscrowReleased({commitment: body.commitment, tokens: body.tokens});
            }
        }
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-714)
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
```
