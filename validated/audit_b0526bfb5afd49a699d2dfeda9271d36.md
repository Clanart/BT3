### Title
`IntentGatewayV2.instance()`/`authenticate()` default to `address(this)` for unregistered chains, allowing escrow release without governance-approved peer registration - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Stakehouse bug let an attacker supply an unverified `_stakingFundsVaults` address that the pool implicitly trusted, letting it call `burnLPTokensForETH` with `GiantMevAndFeesPool` as `msg.sender` and drain real LP tokens. The local analog is `IntentGatewayV2`'s peer-authentication logic: instead of requiring an explicitly governance-registered gateway instance for every remote chain, the `instance()`/`authenticate()` pair silently falls back to trusting `address(this)` for any chain that was never registered, which lets escrow-releasing `RedeemEscrow`/`RefundEscrow` messages be authenticated without the intended one-time `NewDeployment` governance step.

### Finding Description
`authenticate()` gates the two fund-moving `onAccept` branches (`RedeemEscrow`, `RefundEscrow`): [1](#0-0) 

It relies on `instance(request.source)` to resolve the trusted peer gateway for the message's origin chain: [2](#0-1) 

When no gateway has ever been registered for a given `stateMachineId` (i.e., `_instances[keccak256(stateMachineId)] == address(0)`, which is the default for every chain until a `NewDeployment` governance message is dispatched), `instance()` does not fail closed — it returns `address(this)`, silently treating the message as if it came from "itself":

```solidity
function instance(bytes calldata stateMachineId) public view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    return gateway == address(0) ? address(this) : gateway;
}
```

Compare this to the hardened `evm/src/apps/intentsv2/IntentsBase.sol` variant, which explicitly reverts `UnknownInstance()` instead of defaulting to a trusted value: [3](#0-2) 

This is the exact bug class from the report: a security-relevant relationship (which remote contract is allowed to move this contract's escrowed funds) is supposed to be established only through an explicit, governance-gated registration (`RequestKind.NewDeployment`, dispatched only by Hyperbridge itself per the `keccak256(incoming.request.source) != keccak256(hyperbridge())` check), but a permissive default silently substitutes an implicit, unverified trust relationship (`address(this)`) whenever that registration hasn't happened for a given chain. `NewDeployment` registration: [4](#0-3) 

Because `IntentGatewayV2` deployments are explicitly designed to be bytecode-identical and deployed at deterministic CREATE2 addresses across chains (documented in the chain-id branching comment about preserving deterministic addresses), the genuine, permissionless `IntentGatewayV2` contract deployed on *any* chain that Hyperbridge's `Host` can produce valid state/consensus proofs for — but that has not yet been explicitly whitelisted via `NewDeployment` on the victim chain — will, by construction, satisfy `module == address(this)`. Anyone can drive that "unregistered but address-identical" instance through its ordinary, permissionless `fillOrder`/cancel path, reconstructing a victim's real `Order` (its full contents are public via the `OrderPlaced` event) and dispatching a `RedeemEscrow`/`RefundEscrow` message whose `commitment`, `tokens`, and `beneficiary` are attacker-chosen, which then passes `authenticate()` on the victim chain purely due to the `address(this)` fallback — bypassing the intended one-time, Hyperbridge-governed instance-registration control entirely.

### Impact Explanation
If exploited, escrowed input tokens for a real cross-chain intent order can be released (via `withdraw()`) to an attacker-controlled beneficiary before, or entirely independent of, any governance-approved deployment registration for the originating chain — i.e., unauthorized redirection of escrowed user funds (`_orders[commitment][token]`) to the wrong beneficiary. This matches the bounty's "stealing or loss of funds" / "false proof acceptance via wrong module binding" impact category, since it lets a message purportedly from an unauthenticated/unregistered chain instance move real escrow.

### Likelihood Explanation
Exploitation requires: (1) a state machine ID for which Hyperbridge's `Host` on the victim chain can produce valid state proofs (i.e., a chain Hyperbridge already supports at the infra layer) but for which `IntentGatewayV2` governance has not yet dispatched `NewDeployment`, and (2) the ability to drive that chain's identical-bytecode `IntentGatewayV2` deployment through its own permissionless fill/cancel flow to emit the desired `WithdrawalRequest`. This is realistic during chain rollout windows (new chains are frequently supported by the bridge infra before the app-level `NewDeployment` governance call is made) and does not require a malicious relayer, prover, or admin — only a public entrypoint call on a legitimately-deployed, address-identical instance of the same contract.

### Recommendation
Remove the `address(this)` fallback in `instance()`/`authenticate()`; unregistered `stateMachineId`s must revert (as already done correctly in `evm/src/apps/intentsv2/IntentsBase.sol::_instance`), so that escrow-releasing messages are only accepted from chains explicitly whitelisted via the governance-gated `NewDeployment` flow.

### Proof of Concept
1. Victim calls `placeOrder` on Chain A with `order.source = A`, `order.destination = B`, escrowing `inputs` under `commitment = keccak256(abi.encode(order))`; full `order` is public via `OrderPlaced`.
2. Chain A's `_instances` has no entry for some Chain X that Hyperbridge's `Host` on Chain A can nonetheless verify state proofs for (not yet registered via `NewDeployment`).
3. Attacker calls the ordinary, permissionless fill/cancel entrypoint on the identical-bytecode `IntentGatewayV2` deployment on Chain X, supplying the exact copied `order` struct and setting the resulting `WithdrawalRequest.beneficiary` to themselves.
4. The dispatched `RedeemEscrow`/`RefundEscrow` PostRequest arrives at Chain A with `request.source = X`, `request.from = address(thisGateway)`.
5. `authenticate()` calls `instance(X)`, which returns `address(this)` (no entry registered), matching `module`, so the check passes.
6. `withdraw()` releases the victim's real escrowed tokens to the attacker's beneficiary: [5](#0-4) .

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L628-634)
```text
        // only hyperbridge is permitted to perfom these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            NewDeployment memory body = abi.decode(incoming.request.body[1:], (NewDeployment));
            _instances[keccak256(body.stateMachineId)] = body.gateway;

            emit NewDeploymentAdded({stateMachineId: body.stateMachineId, gateway: body.gateway});
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L352-361)
```text
    /**
     * @dev Resolves the IntentGateway instance address for a given state machine.
     * Reverts with `UnknownInstance` if no remote deployment has been registered for that chain.
     * @param stateMachineId The raw state machine identifier bytes.
     * @return The gateway address for the given state machine.
     */
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
```
