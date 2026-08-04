## Title
`IntentGatewayV2.instance()` on Tron falls back to the gateway's own address for unregistered chains, allowing forged cross-chain `RedeemEscrow`/`RefundEscrow` messages to drain escrow - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron variant of `IntentGatewayV2` authenticates incoming `RedeemEscrow`/`RefundEscrow` post requests by comparing the message's claimed sender module against `instance(request.source)`. Unlike the canonical EVM implementation, which reverts with `UnknownInstance` when a source chain has no registered peer gateway, the Tron `instance()` function silently falls back to `address(this)` for any unregistered chain.

### Finding Description
`instance()` in the Tron contract is defined as: [1](#0-0) 

```
function instance(bytes calldata stateMachineId) public view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    return gateway == address(0) ? address(this) : gateway;
}
```

This is used directly for authenticating incoming settlement messages: [2](#0-1) 

and in `onAccept`, `authenticate()` gates the fund-moving `RedeemEscrow`/`RefundEscrow` paths before calling `withdraw()`: [3](#0-2) 

`_instances` is only populated through explicit `NewDeployment` governance messages from Hyperbridge: [4](#0-3) 

By contrast, the canonical EVM `IntentsBase._instance` (used by `evm/src/apps/intentsv2/*`) reverts with `UnknownInstance` when a chain has no registered deployment — it never falls back to `address(this)`: [5](#0-4) [6](#0-5) 

This is the same broken-invariant pattern as the M-3 report: instead of consulting the authoritative, governance-updated per-chain address record, the contract substitutes its own hardcoded/self address when the correct value is missing, silently redirecting authorization/fund flow to the wrong entity. In M-3 the Controller used its own immutable `treasury` instead of the vault's configured treasury; here, the gateway uses its own contract address as a stand-in "trusted sender" instead of requiring an explicitly registered remote instance.

**Attack path:** Hyperbridge tracks many state machines at the host/consensus level independent of whether IntentGatewayV2 has registered a peer for that chain (`NewDeployment` is a separate, asynchronous governance action). During the window before a given remote chain's gateway peer is registered on Tron (or permanently, if governance never registers a particular already-supported chain), any contract deployed on that remote chain whose address happens to equal the Tron gateway's own address will be treated as an authenticated `IntentGatewayV2` instance. An attacker can:
1. Mine/deploy (via `CREATE2` or plain deployment with a chosen nonce/deployer) a malicious contract on a Hyperbridge-supported chain at an address equal to the Tron `IntentGatewayV2`'s address (a public, known 20-byte value).
2. From that contract, dispatch an ISMP POST request to the Tron gateway with `body = [RequestKind.RedeemEscrow, WithdrawalRequest{commitment, tokens, beneficiary: attacker}]` for a real order (or a `RefundEscrow` for a cancellable order).
3. Because `_instances[keccak256(request.source)] == address(0)`, `instance(request.source)` returns `address(this)`, which equals `request.from` — `authenticate()` passes.
4. `withdraw()` releases the escrowed tokens (and any recorded transaction fees) to the attacker-chosen `beneficiary`, exactly as a legitimate solver settlement would.

### Impact Explanation
This is a direct unauthorized-execution / false-acceptance vulnerability on escrowed bridge funds: an unprivileged attacker can forge a cross-chain module identity to trigger `withdraw()` and redirect escrowed input tokens (and accrued fees) to an arbitrary beneficiary, without ever placing a legitimate order or performing a real fill. This matches the bounty's core categories: stealing/loss of funds, unauthorized execution, and false acceptance of cross-chain state due to a wrong module binding.

### Likelihood Explanation
Requires no compromised relayer, prover, or governance key — only a normal ISMP-supported source chain (which Hyperbridge already tracks) and the ability to deploy a contract at a specific address, which is a standard, permissionless operation (`CREATE2`, or iterating deployer nonces). It is directly triggerable any time a chain is live at the protocol/consensus level before (or if) governance registers the Tron gateway's peer for that chain via `NewDeployment` — a routine, expected operational gap, not an edge case.

### Recommendation
Change `instance()` to revert (e.g., `UnknownInstance`) when `_instances[keccak256(stateMachineId)] == address(0)`, matching the canonical EVM `IntentsBase._instance` behavior, instead of defaulting to `address(this)`. Authentication of incoming `RedeemEscrow`/`RefundEscrow` messages must only succeed for explicitly governance-registered peer gateways.

### Proof of Concept
1. Confirm target Hyperbridge-supported chain `C` (host tracks its state machine, but Tron `IntentGatewayV2._instances[keccak256(C)]` is unset, e.g. `address(0)`).
2. On chain `C`, deploy `EvilSender` via `CREATE2` such that `address(EvilSender) == address(TronIntentGatewayV2)`.
3. From `EvilSender`, call `IDispatcher(hostC).dispatch(DispatchPost{ dest: TronChainId, to: abi.encodePacked(address(TronIntentGatewayV2)), body: abi.encodePacked(uint8(RequestKind.RedeemEscrow), abi.encode(WithdrawalRequest({commitment: knownCommitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(attacker)))}))), timeout: 0, fee: 0, payer: address(this) })`.
4. Once relayed and delivered, Tron's `onAccept` calls `authenticate(request)` → `instance(C)` returns `address(this)` (== `EvilSender`'s address) → check passes → `withdraw()` transfers the escrowed `order.inputs` (and fees) to `attacker`.

Note: I was not able to fully trace the deployment/registration lifecycle scripts to confirm whether every production Tron deployment always registers all peers atomically at `initialize` time; if any supported chain is added later or a peer registration is delayed/skipped, this gap is exploitable as described.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L278-284)
```text
    /**
     * @dev Fetch the IntentGateway contract instance for a chain.
     */
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L63-67)
```text
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L94-115)
```text
    /**
     * @dev One-time init (the `initializer` modifier caps it to a single call). Registers the
     * initial cross-chain peers, each bound to `address(this)`; `_instance` reverts with
     * `UnknownInstance` for any chain not registered here or later via `onAccept` governance.
     *
     * @param p The initial gateway configuration parameters.
     * @param peerChains State-machine ids of the cross-chain peers to register. Each is bound to
     * this gateway's own address, identical across chains under deterministic CREATE2, so no peer
     * address is carried in the proxy's init data.
     */
    function initialize(Params memory p, bytes[] memory peerChains) public initializer {
        uint256 peersLength = peerChains.length;
        for (uint256 i = 0; i < peersLength; i++) {
            Deployment memory deployment = Deployment({
                chain: peerChains[i],
                gateway: address(this)
            });
            _addDeployment(deployment);
        }
        _validateParams(p);
        _params = p;
    }
```
