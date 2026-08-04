### Title
`IntentGatewayV2.authenticate()` Defaults to Self-Trust for Unregistered Source Chains, Allowing Escrow Drain via Unconfigured State Machine IDs - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.onAccept()` processes `RedeemEscrow`/`RefundEscrow` messages after calling `authenticate(request)`, which is meant to verify the message came from a known, registered `IntentGatewayV2` instance on the source chain. However, `instance()` silently falls back to `address(this)` when no instance has been registered for a given `stateMachineId`. This mirrors the reported bug class exactly: an action (crediting/releasing escrowed funds) is taken while implicitly assuming a prerequisite trust relationship ("this chain is a registered/known IntentGateway peer") is established, without actually verifying it — just as `channelOpenAck()` assumed the connection was `OPEN` without checking.

### Finding Description
`instance()` and `authenticate()`: [1](#0-0) 

```solidity
function instance(bytes calldata stateMachineId) public view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    return gateway == address(0) ? address(this) : gateway;
}

function authenticate(PostRequest calldata request) internal view {
    if (request.from.length != 20) revert InvalidInput();
    address module = address(bytes20(request.from));
    // IntentGateway only accepts incoming assets from itself or known instances
    if (instance(request.source) != module) revert Unauthorized();
}
```

`_instances[stateMachineId]` is only populated via a privileged `NewDeployment` message dispatched by Hyperbridge itself: [2](#0-1) 

Because `instance()` returns `address(this)` for any `stateMachineId` that has not yet been registered as a known `IntentGateway` deployment, `authenticate()` will accept a `PostRequest` whose `from` field equals `abi.encodePacked(address(this))` — i.e., the victim contract's own address — regardless of which chain the message actually originated from, as long as that chain is one Hyperbridge has a valid consensus client for (which is a much larger set of chains than the set of chains that actually run `IntentGatewayV2`).

This is invoked from `onAccept()`: [3](#0-2) 

which then calls `withdraw()` to transfer real escrowed tokens (`_orders[commitment][token]`) out to an attacker-chosen `beneficiary`: [4](#0-3) 

### Impact Explanation
This is a false-authentication acceptance that leads directly to unauthorized transaction execution and loss of escrowed user funds — matching the bounty's "stealing or loss of funds" and "logic attacks / false proof acceptance" categories. `order.source`/commitments and the corresponding `_orders[commitment][token]` escrow balances are public (readable on-chain, and `OrderPlaced` is emitted with the commitment), so an attacker can target any real, currently-escrowed order and redirect its payout to themselves, or refund it prematurely to an attacker-controlled beneficiary, without ever filling the order or waiting for cancellation conditions.

### Likelihood Explanation
Exploitation requires only: (1) any EVM chain that Hyperbridge has a live/valid consensus client for but on which `IntentGatewayV2` has not (yet) been registered via `NewDeployment` for that `stateMachineId`, and (2) the ability to deploy a trivial contract there and call `IDispatcher.dispatch` with a crafted `PostRequest` (`from = to = victimGatewayAddress`, `dest = <chain hosting the real escrow>`, body = `RedeemEscrow`/`RefundEscrow`). No malicious relayer, prover, governance actor, or leaked key is needed — the proof of the message's origin is entirely legitimate (a real ISMP request from a genuinely connected chain); the flaw is purely in the module-identity binding logic, not in proof verification. This is a fully permissionless, single-transaction attack once such an unregistered-but-connected chain exists, which is plausible during any rollout/expansion phase of the IntentGateway to new chains, or simply for any chain Hyperbridge secures that never gets an IntentGateway deployment.

### Recommendation
Change `instance()`/`authenticate()` so an unregistered `stateMachineId` is explicitly rejected (revert) rather than defaulting to `address(this)`. Trust in a specific `stateMachineId` as a valid IntentGateway peer should only exist after an explicit `NewDeployment` registration for that exact chain; there should be no implicit "self" fallback used for authentication of cross-chain messages, mirroring the same fix pattern as ICS-04 requiring an explicit `STATE_OPEN` check instead of assuming it.

### Proof of Concept
1. Identify a `stateMachineId` `X` for which Hyperbridge has a valid, active consensus client (i.e., `dispatch` from `X` will be relayed and its proofs will verify), but for which no `NewDeployment` has been registered on the victim's `IntentGatewayV2` (deployed on chain `Y`) — i.e. `instance(X) == address(this)` on chain `Y`.
2. On chain `X`, deploy a trivial contract (or use any EOA-triggerable contract call path) that calls `IDispatcher(host).dispatch(...)` with:
   - `dest = Y`
   - `to = abi.encodePacked(victimGatewayAddressOnY)`
   - `from = abi.encodePacked(victimGatewayAddressOnY)` (spoofing "itself" as sender)
   - `body = abi.encodePacked(RequestKind.RedeemEscrow, abi.encode(WithdrawalRequest({commitment: <real known commitment>, tokens: <matching token list/amounts>, beneficiary: attackerAddress})))`
3. Once relayed and proven on chain `Y` through the normal (legitimate) ISMP request flow, `IntentGatewayV2.onAccept()` on `Y` calls `authenticate(request)`; since `instance(X) == address(this) == module`, authentication passes.
4. `withdraw()` executes, transferring the real escrowed tokens for `commitment` to `attackerAddress`, draining funds that belonged to the legitimate solver/user for that order.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L278-294)
```text
    /**
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L628-634)
```text
        // only hyperbridge is permitted to perfom these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            NewDeployment memory body = abi.decode(incoming.request.body[1:], (NewDeployment));
            _instances[keccak256(body.stateMachineId)] = body.gateway;

            emit NewDeploymentAdded({stateMachineId: body.stateMachineId, gateway: body.gateway});
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-713)
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
```
