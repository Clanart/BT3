### Title
`instance()` fallback to `address(this)` allows unauthorized cross-chain escrow authentication for unregistered peer chains - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of `IntentGatewayV2` resolves an unregistered peer-chain gateway address by silently falling back to `address(this)` instead of reverting, unlike the canonical EVM `IntentGatewayV2.sol` implementation which reverts with `UnknownInstance`. This mirrors the "missing factory dependency" bug class: a registration step (binding a remote chain's gateway address into `_instances`) that is expected but not performed, and instead of a hard failure, the missing-binding path degrades into an implicit trust default, which an attacker can leverage for false authentication of a settlement message.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, `instance()` is defined as: [1](#0-0) 

```solidity
function instance(bytes calldata stateMachineId) public view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    return gateway == address(0) ? address(this) : gateway;
}
```

`authenticate()` uses this helper to validate the sender of an incoming `PostRequest`: [2](#0-1) 

```solidity
function authenticate(PostRequest calldata request) internal view {
    if (request.from.length != 20) revert InvalidInput();
    address module = address(bytes20(request.from));
    // IntentGateway only accepts incoming assets from itself or known instances
    if (instance(request.source) != module) revert Unauthorized();
}
```

Contrast this with the canonical (non-Tron) implementation, which correctly treats an unregistered chain as untrusted and reverts: [3](#0-2) 

```solidity
function _instance(bytes calldata stateMachineId) internal view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    if (gateway == address(0)) revert UnknownInstance();
    return gateway;
}
```

The bug-class parallel to the external report: in the zkSync case, a *missing registration* (factory dependency) caused a deployment to *fail* safely. Here, the equivalent missing registration (a peer gateway never added via `NewDeployment`/governance for a given `StateMachine`/chain id) does **not** fail safely — it falls through to `address(this)` as the implicitly-trusted counterpart. Since `request.from` is attacker-controlled data carried in the cross-chain `PostRequest` body/header assembled off-chain and merely checked for a 20-byte length, an attacker who can get a `PostRequest` accepted by the local `IsmpHost` (with any `request.source` value that has never been explicitly registered as a peer, e.g., a chain id that the operator simply never got around to registering, or one no longer supported) and with `request.from` set to the local `IntentGatewayV2` contract's own address, will pass `authenticate()`. `RedeemEscrow`/`RefundEscrow` requests can then be executed for arbitrary commitments, or `_withdraw` reached under actor-controlled disguise, without ever having a real cross-chain instance authorized on that `source`.

### Impact Explanation
This breaks the "module identity binding" invariant that the Hyperbridge pivots specifically call out: request/response paths must bind module/app identity uniquely so that only the rightful, registered peer contract can authorize withdrawal of escrowed funds. Here, any unregistered `source` state machine is silently treated as authorized as long as `request.from` matches the local contract's own address — trivially satisfiable since it's just 20 bytes of attacker-supplied calldata forwarded through the ISMP request pipeline. This enables draining escrowed order funds (`_orders`) via forged `RedeemEscrow`/`RefundEscrow` requests purportedly from a "self" instance on a chain that was never actually deployed/registered, resulting in fund loss/unauthorized execution exactly matching the "Bridged assets... must move exactly once and only to the rightful beneficiary" pivot.

### Likelihood Explanation
Likelihood depends on whether Hyperbridge's `IsmpHost`/message-handling pipeline actually permits delivery of a `PostRequest` whose `source` state machine is not a chain Hyperbridge has consensus/state proofs for at all — normally the state machine must have a valid state commitment/consensus client for the proof to verify, which constrains which `source` values are reachable. However, this pathway is realistic in the exact scenario the external report describes: an operator registers/whitelists a new chain in the host/consensus layer before completing the corresponding `NewDeployment` registration step in `IntentGatewayV2`'s `_instances` mapping (a legitimate, connected, provable chain whose gateway binding step was simply skipped/delayed) — i.e., precisely the same operational gap as "deploy code but forget to register the dependency." In that state, a legitimate relayer can deliver a real, proven message from that connected-but-unregistered chain, and any `from` address collision with the local contract address is sufficient.

### Recommendation
Change `instance()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` to revert (e.g., with `UnknownInstance`) when no gateway is registered for the given `stateMachineId`, matching the canonical `_instance()` behavior in `evm/src/apps/intentsv2/IntentsBase.sol`. Remove the `address(this)` fallback entirely from the authentication path so that unregistered peers can never satisfy `authenticate()`.

### Proof of Concept
1. Deploy `IntentGatewayV2` (Tron variant) and have Hyperbridge's consensus/state-proof layer support state machine `X` (e.g., because `X` is a generally-connected chain), but never call the `NewDeployment` flow to register `_instances[keccak256(X)]`.
2. An attacker (or any relayer capable of delivering a proven message from chain `X`) submits a `PostRequest` with `source = X`, `from = <20 bytes equal to the local IntentGatewayV2 contract's own address>`, and `body` encoding `RequestKind.RedeemEscrow` with a `WithdrawalRequest` referencing an existing escrowed `commitment`.
3. `onAccept` → `_authenticate`/`authenticate` calls `instance(X)`, which returns `address(this)` because `_instances[keccak256(X)] == address(0)`.
4. Since `module == address(this) == instance(X)`, authentication succeeds, and `_withdraw` releases the escrowed tokens to the attacker-chosen beneficiary — despite chain `X` never having had a legitimate registered IntentGateway peer.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L281-284)
```text
    function instance(bytes calldata stateMachineId) public view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        return gateway == address(0) ? address(this) : gateway;
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L286-294)
```text
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L358-362)
```text
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
    }
```
