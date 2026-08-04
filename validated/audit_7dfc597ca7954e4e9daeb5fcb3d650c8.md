### Title
`IntentGatewayV2._fillCrossChain()` hardcodes the RedeemEscrow beneficiary to `msg.sender`, causing loss of escrowed input tokens for smart-contract solvers - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
This is a direct local analog of the reported bug: instead of allowing the filler to specify who should receive the escrowed source-chain assets, `_fillCrossChain()` hardcodes the `WithdrawalRequest.beneficiary` field of the cross-chain `RedeemEscrow` message to `msg.sender` — the solver's address on the *destination* chain.

### Finding Description
When a solver fills a cross-chain order, `_fillCrossChain()` builds the settlement message that will unlock the escrowed input tokens on the source chain: [1](#0-0) 

The `beneficiary` field is populated with `bytes32(uint256(uint160(msg.sender)))`, i.e. the solver's address as it exists on the *destination* chain, with no parameter to let the caller supply a different receiving address for the *source* chain. This request is dispatched as a `RedeemEscrow` POST to the source chain, where `onAccept()` decodes it and calls `_withdraw()`: [2](#0-1) 

`_withdraw()` then transfers the escrowed tokens directly to that address on the source chain, with no way to override it: [3](#0-2) 

If the solver interacts via a smart-contract wallet (multisig, Safe, session-key wallet, account-abstraction contract, etc.), `msg.sender` in `_fillCrossChain()` is that contract's address on the destination chain. The same address on the source chain may not be deployed, may not exist, or may already be controlled by an unrelated party (the classic counterfactual-address collision scenario, per the referenced Wintermute incident). There is no `FillOptions` field for specifying a separate settlement beneficiary — `FillOptions` only carries `relayerFee`, `nativeDispatchFee`, and `outputs`: [4](#0-3) 

This mirrors exactly the pattern in the external report: the destination-chain settlement address is derived from `msg.sender` rather than being an explicit, caller-chosen parameter, so it is only safe for EOAs.

### Impact Explanation
If a smart-contract solver fills an order and its address is not replicated (or is controlled by someone else) on the source chain, the escrowed input tokens released by `_withdraw()` are sent to an address the solver cannot access, resulting in a direct, unrecoverable loss of the escrowed funds — or in the worst case, an attacker who controls (or can later deploy to) that address on the source chain steals the released tokens. This satisfies the "stealing or loss of funds" / "wrong beneficiary" impact criteria for cross-chain settlement.

### Likelihood Explanation
Likelihood is medium: it requires the filler to route order fills through a smart-contract wallet whose deployed address differs across chains (a common real-world setup for Safe multisigs, AA wallets, and session-key based fillers used by solvers/market-makers), matching the same "multisig wallet contract" precondition as the original report — no malicious relayer, prover, or governance actor is needed, and the attacker/victim scenario is triggered purely by unprivileged interaction with the public `fillOrder`/`_fillCrossChain` path.

### Recommendation
Add an explicit `beneficiary` (or `settlementReceiver`) field to `FillOptions` that the filler supplies at fill time, and use that value — rather than `msg.sender` — when constructing the `WithdrawalRequest.beneficiary` in `_fillCrossChain()`. Validate the field is non-zero, and document that it may differ from `msg.sender` to support cross-chain smart-contract solver operation.

### Proof of Concept
1. A solver operates via a Safe/multisig contract `S` deployed at address `0xABC` on the destination chain (e.g., Arbitrum).
2. The solver calls `fillOrder(order, options)` from `S`; `_fillCrossChain()` sets `_filled[commitment] = msg.sender` and encodes `WithdrawalRequest.beneficiary = bytes32(uint256(uint160(0xABC)))` [5](#0-4) , then dispatches the `RedeemEscrow` POST to the source chain [1](#0-0) .
3. On the source chain (e.g., Ethereum), `0xABC` is either undeployed or is a completely different, attacker-controlled EOA/contract (Safe multisig addresses are deterministic per-chain based on factory nonce/salt and deployment history, and can diverge across chains).
4. `onAccept()` on the source-chain `IntentGatewayV2`/`ExtrinsicIntents` authenticates the message as coming from the legitimate peer gateway and calls `_withdraw()`, which unconditionally transfers the escrowed input tokens to `0xABC` [6](#0-5) .
5. The legitimate solver has no way to recover the funds unless they happen to control `0xABC` on the source chain as well; an attacker who deploys/controls that address on the source chain can claim the escrowed tokens outright.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L92-93)
```text
        _filled[commitment] = msg.sender;

```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L139-155)
```text
        address hostAddr = host();
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-295)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }
```

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
