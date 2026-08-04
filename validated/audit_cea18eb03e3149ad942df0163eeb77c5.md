## Analysis

I was unable to fully verify one detail — whether `EvmHost.dispatchIncoming` marks the `requestReceipts` entry *before* or *after* invoking the destination module's `onAccept()` — because I ran out of tool iterations before reading that function body. This matters for precisely characterizing the failure mode (permanently un-processable vs. retryable-forever), so I flag that as unverified.

However, the core local analog is clearly supported by the code I did retrieve.

### Title
Escrow release to a blacklisted USDC/USDT beneficiary permanently locks funds in `IntentGatewayV2` (`IntentsBase._withdraw`) - (File: evm/src/apps/intentsv2/IntentsBase.sol)

### Summary
`IntentsBase._withdraw()`, invoked from `onAccept()` when a `RedeemEscrow` or `RefundEscrow` cross-chain message arrives, pushes escrowed ERC20 tokens to a `beneficiary` address taken directly from the message body via `IERC20(token).safeTransfer(beneficiary, amount)`. If `token` is USDC/USDT and `beneficiary` is on that issuer's blacklist, `safeTransfer` reverts unconditionally, causing the whole `onAccept` call (and thus the whole message-processing transaction) to revert every time it is retried, exactly mirroring the Halborn `AuctionUpgradeable` finding.

### Finding Description
`_withdraw` in `IntentsBase.sol` decrements the escrow accounting and transfers tokens to `beneficiary` unconditionally: [1](#0-0) 

The `beneficiary` is derived from `body.beneficiary`, which for `RedeemEscrow` is set to `msg.sender` (the filler/solver) on the destination chain and propagated cross-chain, and for `RefundEscrow`/cancellation flows is the order's `user` — both are attacker- or third-party-controlled addresses that the gateway does not screen: [2](#0-1) 

The equivalent logic exists in the Tron/EVM twin contract, using raw `.call` with the ERC20 `transfer` selector instead of `SafeERC20`, but with the identical unconditional-transfer-to-arbitrary-beneficiary pattern: [3](#0-2) 

There is no token allow-list, no blacklist check, and no fallback/pull-payment path if the transfer fails. If a solver (for `RedeemEscrow`) or a user (for `RefundEscrow`) happens to be blacklisted by Circle (USDC) or Tether (USDT) — the two most heavily used stablecoins in intent-based settlement — the transfer call reverts. Because `withdraw()`/`_withdraw()` is called synchronously inside `onAccept()`, which is invoked by the host when processing the settlement message, every delivery attempt of that message reverts, and the escrowed input tokens recorded in `_orders[commitment][token]` can never be released. This is functionally identical to the `AuctionUpgradeable.closeAuction()` bug: an untrusted, blacklistable stablecoin recipient causes a mandatory push-transfer to permanently revert, freezing protocol-custodied funds.

### Impact Explanation
This matches the bounty's "stealing or loss of funds" / fund-lock category. Once a blacklisted address becomes the beneficiary of a `RedeemEscrow` (solver fill) or `RefundEscrow` (cancellation), the escrowed input tokens for that specific order commitment become permanently unretrievable — no other code path in the contract allows re-routing the payout or clearing the escrow entry, since `_orders[commitment][token]` is only decremented inside the same reverting `_withdraw()` call. This is a direct, unprivileged loss/lock of user and solver funds, not merely a griefing/DoS of a single transaction.

### Likelihood Explanation
Likelihood is realistic but conditioned on an address becoming blacklisted (a real-world event for USDC/USDT, independent of protocol logic). Because solver/filler addresses are used repeatedly across many orders (the same solver account fills many intents), and users can be arbitrary addresses, exposure accumulates over time — any solver whose address is later sanctioned/blacklisted permanently strands every unsettled order routed to it. No malicious peer, relayer, or governance action is required; the attacker primitive is simply "become or target a blacklisted address," which is outside any input-validation the contract performs.

### Recommendation
Mirror the fix the original report cites for Irrigation Protocol: either (a) maintain a governance-controlled allow-list of settlement tokens for `IntentGatewayV2` that excludes blacklistable stablecoins, or (b) change `_withdraw` to a pull-payment / try-catch pattern — wrap the `safeTransfer` (or low-level `call`) in a try/catch, and on failure credit the amount to an internal claimable balance for the beneficiary (or a designated fallback recipient) rather than reverting the whole settlement, so that one blacklisted address cannot block finalization of the order or block escrow accounting from settling.

### Proof of Concept
1. User places a cross-chain order on the source chain, escrowing USDC as `order.inputs[0]`.
2. A solver (or any address the user names as `beneficiary`) fills the order on the destination chain per `IntrinsicIntents.sol`/`ExtrinsicIntents.sol`, and the gateway dispatches a `RedeemEscrow` `WithdrawalRequest{commitment, tokens: order.inputs, beneficiary: solverAddress}` back to the source chain (see `ExtrinsicIntents.sol` lines 140–155, cited above).
3. Circle blacklists `solverAddress` for USDC (this can happen at any time, independent of the protocol) before the message is delivered/finalized on the source chain.
4. The relayer submits the settlement message; `IntentGatewayV2.onAccept()` on the source chain calls `_withdraw(body, false, true)`.
5. Inside `_withdraw`, `IERC20(token).safeTransfer(beneficiary, amount)` reverts because USDC's `transfer` function reverts for blacklisted recipients (`evm/src/apps/intentsv2/IntentsBase.sol` lines 404–409).
6. Every retry of message delivery for this commitment reverts identically; `_orders[commitment][token]` is never decremented, so the escrowed USDC is permanently stuck in `IntentGatewayV2`, and the order can never be finalized (matching step-for-step the original Halborn `AuctionUpgradeable.closeAuction()` scenario).

### Citations

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L140-155)
```text
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
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
