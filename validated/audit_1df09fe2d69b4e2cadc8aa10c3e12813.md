Based on the evidence gathered, the strongest local analog is in the IntentGateway escrow-refund path, where the refund beneficiary is permanently fixed to the order's `user` field with no way to redirect funds if that address is blacklisted by the escrowed ERC-20 token contract.

### Title
Intent order refunds/settlements permanently lock escrowed tokens if `order.user` or the fill beneficiary is blacklisted by the input/output token contract - (File: evm/src/apps/intentsv2/IntentsBase.sol, evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`IntentsBase._withdraw()` (and its tron-fork twin `IntentGatewayV2.withdraw()`) always sends escrowed tokens to `beneficiary = address(uint160(uint256(body.beneficiary)))`, which for the `RefundEscrow` and `RedeemEscrow` cross-chain paths is hard-set to `order.user` (the user who originally placed the order) at order-creation time. There is no mechanism, at cancellation/timeout time, for the beneficiary to redirect the refund to a different address. If the ERC-20 token used as escrow blacklists `order.user` between order placement and refund/settlement, the `safeTransfer`/low-level `transfer` call reverts and the escrowed principal becomes permanently stuck in the gateway contract, exactly mirroring the Ajna report's core invariant break: "principal owed to a fixed on-chain beneficiary address with no way to specify an alternate recipient."

### Finding Description
`_withdraw` in `evm/src/apps/intentsv2/IntentsBase.sol` (lines 390-425) unconditionally transfers each escrowed input token to `beneficiary`: [1](#0-0) 

The `beneficiary` value used in the refund/cancel flows (`_cancelFromSource`, `_cancelFromDest`, `_cancelSameChain`) is always `order.user`, taken verbatim from the order struct signed/submitted at placement time: [2](#0-1) [3](#0-2) [4](#0-3) 

The tron-chain fork of the gateway has the identical pattern in its `withdraw()` internal function, which reverts the whole transaction (`revert TransferFailed()`) if the ERC-20 `transfer` call to `beneficiary` fails: [5](#0-4) 

Because `order.user` is baked into the order at placement and cannot be changed later (there is no `recipient`/override parameter accepted by `cancelOrder`, `_cancelFromDest`, `onGetResponse`, or `onAccept`), a token-level blacklist against that specific address that occurs anytime after order placement (but before the refund/settlement is finalized) makes the escrow permanently unrecoverable — the `withdraw`/`_withdraw` call will always revert on the transfer step, and there is no alternate code path to pull the funds out to a different address.

This directly parallels the Ajna finding's core broken invariant: a principal-owner's own funds are moved to a hardcoded address (`order.user` here, `msg.sender` there) with no allowance for the beneficiary to nominate a different recipient before/at withdrawal time, even though the underlying value legitimately belongs to them.

### Impact Explanation
If the escrow token blacklists `order.user` (e.g., USDC/USDT compliance blacklist, a common occurrence for stablecoins used as intent inputs), the user's escrowed principal is permanently frozen inside the `IntentGateway`/tron `IntentGatewayV2` contract. This is a genuine, unrecoverable loss-of-funds condition matching the bounty's "stealing or loss of funds" and "bridged assets ... must move exactly once and only to the rightful beneficiary" criteria — the funds move to nobody, forever, once the pre-condition triggers. Because both the fill-settlement path (`RedeemEscrow`) and the cancel/refund path (`RefundEscrow`, same-chain cancel) route through the same beneficiary-locked `_withdraw`/`withdraw`, both the user's refund and (separately) a solver's settlement payout are exposed to the same failure mode, with no admin sweep or override function to specify an alternate beneficiary for a stuck order.

### Likelihood Explanation
This requires an external event (the beneficiary being blacklisted by the token contract) which, as the original report notes, is a low-probability but realistic occurrence for centrally-administered stablecoins (USDC, USDT) that are the most common Intent Gateway settlement assets. No malicious relayer, prover, or governance actor is needed — it is purely a function of the token issuer's independent compliance action landing between order placement and refund/settlement finalization, which can span an arbitrary amount of time (a cross-chain order can sit unfilled until `order.deadline`, and cancellation can be initiated well after that).

### Recommendation
Add an explicit "stuck order" recovery path decoupled from the single hardcoded beneficiary: e.g., allow `order.user` to designate/rotate a recipient address for refunds at cancellation time (via a signed message, similar to how `pallet-relayer`'s `withdrawal.rs` already supports an optional `beneficiary: Option<Vec<u8>>` override for relayer fee withdrawals), or fall back to a pull-based `claimable[commitment][token]` accounting model when the push transfer in `_withdraw`/`withdraw` reverts, so a later transaction from an alternate address can retrieve the funds instead of reverting the whole settlement and locking the escrow forever.

### Proof of Concept
1. User places a cross-chain order via `placeOrder`, escrowing USDC as input, with `order.user = <userAddress>`.
2. Before the order is filled, USDC issuer blacklists `<userAddress>`.
3. `order.deadline` passes without a fill; user (or, after deadline, anyone) calls `cancelOrder()` → `_cancelFromDest`/`_cancelFromSource`, which eventually invokes `_withdraw(body, true, true)` in `IntentsBase.sol` (or `withdraw(body, true)` in the tron fork) with `body.beneficiary = order.user`.
4. `IERC20(token).safeTransfer(beneficiary, amount)` (`IntentsBase.sol:408`) or the low-level `token.call(transfer(...))` (`IntentGatewayV2.sol:697`) reverts because `beneficiary` is blacklisted.
5. The entire refund transaction reverts; `_orders[commitment][token]` is never decremented, so the escrow remains in the contract with no way to move it out, since every code path that releases these funds uses the same fixed `beneficiary`. [1](#0-0) [6](#0-5)

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
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
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L188-223)
```text
    function _cancelFromSource(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        if (options.height <= order.deadline) revert NotExpired();

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-267)
```text
    function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.deadline >= _blockNumber()) {
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
        }

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

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L161-187)
```text
    function _cancelSameChain(Order calldata order, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        if (orderSource != currentChain) revert WrongChain();

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
