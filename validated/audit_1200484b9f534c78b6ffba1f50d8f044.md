### Title
Multi-token escrow settlement permanently reverts and locks all escrowed funds when a single input token blocks transfer - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
`IntentsBase._withdraw` (invoked from `onAccept` for both `RedeemEscrow` and `RefundEscrow`, and from `onGetResponse`) iterates over every escrowed input token of an order and performs a transfer to the beneficiary inside a single loop with no per-token isolation. Exactly like the SafEth `unstake` loop that reverted for *all* derivatives when one (`WstEth`) could no longer transfer, a single "bad" token in an order's `inputs` array (blacklist-capable token such as USDC/USDT, a paused token, or a token that later reverts on transfer to a specific address) causes the entire settlement message to revert, and since this handler is reached only from a one-time cross-chain `PostRequest`/`GetResponse` delivery, the failure is not retried with a corrected token set — it fails identically every time it is redelivered, permanently locking every other (otherwise-transferable) token escrowed in the same order.

### Finding Description
`_withdraw` in `evm/src/apps/intentsv2/IntentsBase.sol` (lines 391-425) is the single code path used to release escrow for:
- `RedeemEscrow` (solver fill payout) via `onAccept` in `ExtrinsicIntents.sol` line 294
- `RefundEscrow` (cancel/refund to user) via `onAccept` in `ExtrinsicIntents.sol` line 294
- Cancel-from-source flow via `onGetResponse` in `ExtrinsicIntents.sol` line 323

```solidity
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
``` [1](#0-0) 

`IERC20.safeTransfer` reverts the entire outer transaction if the underlying token reverts (e.g. a USDC-style blacklist on the beneficiary, a paused token, a token requiring KYC/allowlisting, or any custom token that can be made to revert on transfer to a particular address after order placement). Because the loop has no try/catch or per-token isolation, one poisoned token entry reverts the transfer of *every* other token in the same `body.tokens` array — exactly the SafEth `unstake` failure mode where one malfunctioning derivative (`WstEth`) blocked withdrawal of all healthy derivatives.

`onAccept` (`ExtrinsicIntents.sol` line 289) calls `_withdraw` directly (`return _withdraw(...)`), so a revert here propagates and reverts the entire incoming message dispatch. On the EVM host side, `dispatchIncoming` wraps the app call in a low-level `.call` and, on failure, deletes the request receipt "so it can be retried" (`evm/src/core/EvmHost.sol` lines 809-816) — but a retry re-executes the identical `_withdraw` call with the identical poisoned token, so it fails identically forever. There is no mechanism in `IntentsBase`/`ExtrinsicIntents`/`IntrinsicIntents` to skip or remove the offending token and settle the remaining tokens, unlike `SafEth.adjustWeight`'s (still-insufficient) partial mitigation.

### Impact Explanation
This matches the required "stealing or loss of funds" impact class. An order with multiple input tokens (e.g. USDC + DAI) becomes permanently unsettleable in both directions once any single input token can be made to revert transfers to the resolved beneficiary:
- The solver who filled the order on the destination chain can never claim any of the escrowed inputs (not just the poisoned token) via `RedeemEscrow`.
- The user can never get a refund via `RefundEscrow`/`onGetResponse` either, because the same `_withdraw` loop over the same `order.inputs` is used for refunds and hits the same reverting token.
- All escrowed value for the order (all tokens, not just the blocked one) is permanently locked in the contract with no recovery path, since `_filled[commitment]` is never durably set (the whole tx reverts) so the order can be neither refilled, cancelled successfully, nor swept.

### Likelihood Explanation
The path is reachable without any admin, relayer, or prover compromise — it only requires an *unprivileged user or attacker to place a multi-token order* using at least one token capable of blocking transfers to a specific address after placement (USDC/USDT blacklist, a token with pausable/allowlist transfer hooks, or a token the attacker deploys and controls as one of several inputs when griefing their own or another's fill). Since fillers/solvers are permissionless and the order's `inputs` are chosen at `placeOrder` time by the user (attacker-controlled for self-griefing, or exploitable to trap a solver's expected payout for cross-chain fills where solver has already delivered real value on the destination chain before this message settles), this is directly reachable via the public `placeOrder`/`fillOrder`/`cancelOrder` entrypoints without needing a malicious relayer or governance actor.

### Recommendation
Isolate each token transfer in `_withdraw` (e.g. use a low-level `call` with try/catch semantics per token, similar to how `EvmHost.dispatchIncoming` isolates app callbacks) so a single non-transferable token cannot block settlement of the other escrowed tokens. Track partially-settled tokens (e.g. per-token "claimed" flags in `_orders`) so a failed token transfer can be retried or separately reclaimed without re-attempting the tokens that already succeeded, and provide an escape hatch (e.g. governance-gated force-sweep to a recovery address) for the specific poisoned token entry, mirroring the SafEth mitigation direction of allowing removal/isolation of a malfunctioning component rather than requiring every component to always succeed.

### Proof of Concept
1. Attacker deploys `EvilToken`, an ERC20 that reverts on `transfer`/`transferFrom` when the recipient equals a specific address it controls the trigger for (or simply always reverts after a flag is flipped by the attacker).
2. Attacker (as `user`) calls `IntentGatewayV2.placeOrder` with `order.inputs = [USDC(1000), EvilToken(1)]`, escrowing both tokens.
3. A solver fills the order cross-chain via `fillOrder`, delivering the required outputs to the beneficiary and triggering a `RedeemEscrow` dispatch back to the source chain.
4. Before/at settlement time, the attacker flips `EvilToken` into its reverting state (or it was always the case for a blacklist-style token whose target got blacklisted).
5. When the settlement message reaches the source chain and `onAccept` → `_withdraw` executes, the loop transfers `USDC` successfully, then reaches `EvilToken.transfer` which reverts, reverting the whole `_withdraw` call and hence the whole `onAccept`.
6. The message is marked deliverable-but-failed and can be retried indefinitely (`EvmHost.dispatchIncoming` deletes the receipt on failure), but every retry hits the same revert on `EvilToken`.
7. Result: the solver never receives the escrowed 1000 USDC despite having paid out the order's outputs on the destination chain, and the user can never recover it either — permanent fund lock for the entire order, triggered by a single non-cooperating input token, analogous to the SafEth `unstake` DOS via one malfunctioning derivative. [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L391-425)
```text
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
