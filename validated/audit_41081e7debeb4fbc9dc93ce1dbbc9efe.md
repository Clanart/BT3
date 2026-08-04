## Analysis

The external report's core broken invariant: a value that defaults to a "special" identity (a master account) bypasses a check that assumes a real per-record registration, letting a caller satisfy an authorization gate that was never meant to be trivially satisfiable. The local analog in Hyperbridge is the `instance()` fallback in the Tron `IntentGatewayV2`, which silently maps *every unregistered source chain* to the gateway's own address, and that self-identity is then used as the authentication target for cross-chain settlement.

### Title
Unregistered-chain fallback in `IntentGatewayV2.instance()` lets an attacker impersonate the trusted gateway instance and drain escrowed orders - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.instance()` on the Tron/EVM-variant gateway returns `address(this)` for any state machine that has no registered instance, instead of reverting like the canonical implementation does. `authenticate()` uses this same function to validate the `from` field of incoming `RedeemEscrow`/`RefundEscrow` requests, so an unregistered source chain's authentication requirement collapses to "the request must claim to be from `address(this)`" — a condition an attacker can satisfy by deploying the same CREATE2-deterministic gateway bytecode (with themselves as admin) on any Hyperbridge-supported chain that has not yet had `NewDeployment` registered against this destination.

### Finding Description
`instance()`:
```solidity
function instance(bytes calldata stateMachineId) public view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    return gateway == address(0) ? address(this) : gateway;
}
``` [1](#0-0) 

is used directly in the authentication gate for incoming cross-chain settlement messages:
```solidity
function authenticate(PostRequest calldata request) internal view {
    if (request.from.length != 20) revert InvalidInput();
    address module = address(bytes20(request.from));
    // IntentGateway only accepts incoming assets from itself or known instances
    if (instance(request.source) != module) revert Unauthorized();
}
``` [2](#0-1) 

For any `request.source` for which `_instances[keccak256(source)]` has not yet been set via a `NewDeployment` governance message, `instance()` returns `address(this)` — the destination gateway's own address — rather than reverting. Since this destination contract is deployed deterministically via CREATE2 (same salt/init code across chains, per the project's own design), an attacker can deploy the identical `IntentGatewayV2` bytecode (self-appointing themselves as `_admin` via the constructor) on any Hyperbridge-supported chain that this destination has not yet registered as a known instance. That attacker-controlled instance is then indistinguishable from a "real" registered instance for the purposes of `authenticate()`, because its address equals `address(this)` on the destination and `instance(source)` for that unregistered source also resolves to `address(this)`.

Contrast this with the canonical resolver used by the primary EVM app (`IntentsBase.sol`), which explicitly reverts instead of defaulting to self:
```solidity
function _instance(bytes calldata stateMachineId) internal view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    if (gateway == address(0)) revert UnknownInstance();
    return gateway;
}
``` [3](#0-2) 

Using this forged identity, the attacker dispatches a `RedeemEscrow` or `RefundEscrow` `PostRequest` naming any `commitment`/`beneficiary` of their choosing. `onAccept` routes it straight to `withdraw`, which pays out from `_orders[commitment][token]` to the attacker-chosen beneficiary without any further ownership check:
```solidity
function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
    RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
    if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
        authenticate(incoming.request);
        WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
        return withdraw(body, kind == RequestKind.RefundEscrow);
    }
``` [4](#0-3) 
```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;
    uint256 len = body.tokens.length;
    for (uint256 i; i < len;) {
        address token = address(uint160(uint256(body.tokens[i].token)));
        uint256 amount = body.tokens[i].amount;
        if (_orders[body.commitment][token] == 0) revert UnknownOrder();
        ...
        _orders[body.commitment][token] -= amount;
``` [5](#0-4) 

The only gate protecting this transfer is `authenticate()`, and it is defeated by the `instance()` self-fallback for any chain not yet formally onboarded via `NewDeployment`.

### Impact Explanation
This is unauthorized execution and direct fund loss: an attacker can drain any order's escrowed input tokens (and accumulated `TRANSACTION_FEES`) sitting in `_orders[commitment][...]` on the destination `IntentGatewayV2`, to any beneficiary they choose, without going through the real fill/settlement flow or owning the order. It requires no compromised relayer, prover, or admin key — only that the destination gateway currently trusts a source chain identifier for which no legitimate instance has yet been registered (a normal, temporary state during any multi-chain rollout, or simply any Hyperbridge-supported chain the team hasn't deployed to yet).

### Likelihood Explanation
Exploitability depends only on (a) a state machine existing in Hyperbridge's supported set without a corresponding `NewDeployment` entry on this specific destination gateway, and (b) the attacker being able to deploy the same CREATE2-addressed bytecode on that chain and dispatch an ISMP `PostRequest` from it — both of which are unprivileged, permissionless actions available to any user. This is a realistic and likely-to-occur window since gateway rollout to new chains is inherently staggered relative to Hyperbridge's own chain support.

### Recommendation
Make `instance()` revert (as `IntentsBase._instance()` already does) when no instance is registered for the given `stateMachineId`, instead of defaulting to `address(this)`. `authenticate()` should never treat an unregistered source chain as implicitly authorized.

### Proof of Concept
1. Identify a state machine `S` supported by Hyperbridge's consensus/state proof infrastructure for which the target `IntentGatewayV2` instance `G` on destination chain `D` has no entry in `_instances[keccak256(S)]` (no `NewDeployment` processed yet for `S`).
2. On chain `S`, deploy `IntentGatewayV2` using the same CREATE2 deployer address, salt, and init code that produced `G`'s address on other chains, passing `attacker` as the constructor `admin`. The resulting contract address equals `G`'s address (deterministic CREATE2).
3. From this attacker-controlled contract on `S`, dispatch an ISMP `PostRequest` to `D`/`G` with body `bytes.concat(bytes1(uint8(RequestKind.RedeemEscrow)), abi.encode(WithdrawalRequest({commitment: <victim_order_commitment>, tokens: <victim_order_inputs>, beneficiary: bytes32(uint256(uint160(attacker)))})))`.
4. Once relayed and proven on `D`, `G.onAccept` calls `authenticate()`: `request.source == S`, `instance(S)` returns `address(this)` (unregistered fallback) `== module` (the attacker's contract address, which equals `G`'s own address) → authentication passes.
5. `withdraw()` executes, transferring the victim order's escrowed tokens to the attacker's chosen beneficiary.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L281-284)
```text
    function instance(bytes calldata stateMachineId) public view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        return gateway == address(0) ? address(this) : gateway;
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L289-294)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-703)
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
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L358-362)
```text
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
    }
```
