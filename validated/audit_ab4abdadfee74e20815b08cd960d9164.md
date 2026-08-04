## Analysis

The external report's core defect: a validation check reads a value (`vault.borrowed_token()`) before it has been initialized, so the check either always fails or (in a security-critical direction) silently accepts a default/zero value it shouldn't. The exploitable analog in Hyperbridge is in `IntentGatewayV2.sol`'s `instance()`/`authenticate()` pair, where an unmapped (uninitialized) `_instances` entry defaults to `address(this)` instead of failing closed, and this default is used as the trust anchor for authenticating a fund-releasing cross-chain message.

### Title
Uninitialized `_instances` mapping self-defaults to `address(this)`, letting any unregistered source chain forge escrow redemption/refund requests - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`instance(stateMachineId)` returns `address(this)` whenever `_instances[keccak256(stateMachineId)]` has not been explicitly set. `authenticate()` uses this value as the sole trust check for `RedeemEscrow`/`RefundEscrow` requests. Because the "not yet configured" state and "trusted peer is myself" state are indistinguishable, any source chain that Hyperbridge already bridges but for which this specific IntentGateway pairing was never registered lets an attacker forge a message that authenticates as if it came from the gateway itself. [1](#0-0) 

### Finding Description
`instance()` is meant to look up the known IntentGateway deployment address for a given remote state machine: [2](#0-1) 

`authenticate()` is the only gate protecting `RedeemEscrow` and `RefundEscrow` handling in `onAccept`: [3](#0-2) 

Note that unlike `NewDeployment`/`UpdateParams`/`SweepDust`, which additionally require `request.source == hyperbridge`, the `RedeemEscrow`/`RefundEscrow` branch relies *only* on `authenticate()`: [4](#0-3) 

The bug: `instance()` cannot distinguish "this remote chain's gateway pairing has not yet been registered" from "the trusted peer on that chain is this very contract." Both states return `address(this)`. Because `request.from` is attacker-controlled data carried inside the ISMP `PostRequest` body (dispatched permissionlessly by anyone from the source chain's real `IsmpHost`), an attacker can:

1. Pick any state machine `X` for which Hyperbridge already runs a legitimate, trusted consensus client, but for which this destination gateway has never received a `NewDeployment` message registering `X`'s IntentGateway address (e.g., a chain where the gateway app was never deployed, or simply not yet paired).
2. From an ordinary account on chain `X`, dispatch a genuine ISMP `PostRequest` to `X`'s real `IsmpHost`, setting `to` = the destination IntentGatewayV2's address, and `from` = the destination IntentGatewayV2's own address (public, known bytecode/address), with `body` = a `RedeemEscrow`/`RefundEscrow`-tagged `WithdrawalRequest` naming an existing order `commitment` and `beneficiary` = attacker.
3. Any ordinary, non-malicious relayer delivers this message with a normal, valid state proof for chain `X` — no relayer/prover collusion is required, since the message is structurally indistinguishable from a legitimate one.
4. At the destination, `onAccept` calls `authenticate(request)`; `instance(X)` returns `address(this)` because `_instances[keccak256(X)]` was never set; `module` decoded from `request.from` is also `address(this)`; the check passes.
5. `withdraw()` is invoked and pays out the escrowed tokens under `_orders[commitment][token]` to the attacker's `beneficiary`, provided that commitment still has non-zero escrow (i.e., a real order that hasn't been redeemed yet). [5](#0-4) 

This is exactly the pivot called out for this bounty: "Cross-chain admin or host-management effects must not be reachable through malformed proofs, wrong module bindings, or unauthenticated message flow" — here the wrong module binding is the mapping default itself.

### Impact Explanation
An attacker can drain escrowed order funds (`_orders[commitment][token]`) meant for the legitimate filler/user of any still-outstanding order, by redirecting the withdrawal `beneficiary` to themselves. This is a direct theft/loss-of-funds and unauthorized-execution primitive reachable by an unprivileged attacker using only standard, permissionless ISMP dispatch on any chain where this specific gateway pairing happens to be unset — no malicious relayer, prover, or governance actor is required.

### Likelihood Explanation
The precondition — a supported Hyperbridge chain where the destination `IntentGatewayV2`'s `_instances` entry for that source chain is still zero — is the default/initial state for every chain pairing until governance explicitly dispatches a `NewDeployment` message. This is a normal, expected operational window (new chain onboarding, gateway app rollout lagging core Hyperbridge deployment, or deliberately never pairing a chain that still shares the same trusted consensus infrastructure), making the precondition realistic rather than contrived.

### Recommendation
Make `instance()` fail closed instead of defaulting to self: return `address(0)` (or revert) when the mapping is unset, and have `authenticate()` reject any request whose resolved `instance` is the zero address, rather than silently treating "unregistered" as "trusted." If self-referential same-chain messages are intentionally needed, gate that case explicitly on `request.source == host()`'s own chain id rather than on an unset mapping default.

### Proof of Concept
1. Attacker identifies chain `X`, a Hyperbridge-supported state machine where destination `IntentGatewayV2` (chain `D`) has `_instances[keccak256(X)] == address(0)` (verifiable via the public `_instances` getter or `instance(X)` view call, which returns `address(this)`).
2. Attacker finds/awaits a legitimate order on chain `D` with commitment `C` and non-zero `_orders[C][token]` escrow (order data is public via `OrderPlaced` events / order commitment).
3. On chain `X`, attacker calls `IsmpHost.dispatch(PostRequest)` with:
   - `dest` = `D`
   - `to` = `abi.encodePacked(intentGatewayD)`
   - `from` = `abi.encodePacked(intentGatewayD)` (the destination gateway's own address)
   - `body` = `abi.encodePacked(uint8(RequestKind.RedeemEscrow), abi.encode(WithdrawalRequest({commitment: C, tokens: <order tokens>, beneficiary: attacker})))`
4. A normal relayer delivers this request to `D`'s `IsmpHost` with a genuine state proof for chain `X`.
5. `IntentGatewayV2(D).onAccept` decodes `kind = RedeemEscrow`, calls `authenticate(request)`. `instance(X)` returns `intentGatewayD` (self-default), `module` decoded from `from` is also `intentGatewayD` → check passes.
6. `withdraw()` transfers the escrowed tokens for commitment `C` to `attacker`, emitting `EscrowReleased`. [3](#0-2) [6](#0-5)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L279-294)
```text
     * @dev Fetch the IntentGateway contract instance for a chain.
     */
    function instance(bytes calldata stateMachineId) public view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        return gateway == address(0) ? address(this) : gateway;
    }

    /**
     * @dev Checks that the request originates from a known instance of the IntentGateway.
     */
    function authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        // IntentGateway only accepts incoming assets from itself or known instances
        if (instance(request.source) != module) revert Unauthorized();
    }
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L628-629)
```text
        // only hyperbridge is permitted to perfom these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
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
```
