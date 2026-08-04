## Finding: Unprotected, non-atomic `initialize()` on `IntentGatewayV2` allows front-run hijack of host/dispatcher trust anchors (Tron deployment path) - ([File: evm/src/apps/IntentGatewayV2.sol])

### Summary
This is a direct analog of the zAuction bug: `IntentGatewayV2.initialize()` is a public, unauthenticated one-time initializer (guarded only by OZ's `initializer` modifier, not by `msg.sender`), and in the Tron deployment flow the contract is deployed as a **standalone (non-proxy) contract**, with `initialize()` called as a **separate, later transaction**. This reproduces exactly the condition the report flags: deployment and initialization are not atomic, and anyone can front-run the init call to seize control of the gateway's trust configuration.

### Finding Description
`IntentGatewayV2.initialize` is declared: [1](#0-0) 

The `initializer` modifier only prevents a *second* call — it enforces no caller restriction at all, unlike `HostManager.setIsmpHost` (`restrict(_params.admin)`) or `EvmHost.initialize` (`if (_msgSender() != _hostParams.admin) revert`). The code comment explicitly rationalizes this as safe *only* because "atomic CREATE2 deployment already binds the init data to the canonical address": [2](#0-1) 

That safety assumption holds for the EVM deploy script, which bundles `initData` into the same `ERC1967Proxy` constructor call as the CREATE2 deployment, making deployment+init atomic and un-front-runnable: [3](#0-2) 

However, the **Tron migration deploys `IntentGatewayV2` directly (no proxy at all)** and calls `initialize` in a **separate transaction after a block-confirmation wait**: [4](#0-3) 

This is precisely the zAuction pattern: deploy now, initialize later, with an init function that has zero caller restriction. Any address watching the Tron mempool for the deployment can submit its own `initialize(Params, peerChains)` call with attacker-chosen `host`, `dispatcher`, and `priceOracle` addresses before the legitimate `initialize` transaction lands. The legitimate deployer's subsequent call reverts (`Initializable.InvalidInitialization`), so — exactly as the original report notes — the condition is detectable only *after* the fact, once the gateway is already poisoned.

### Impact Explanation
`_params.host` is the trust anchor gating `onAccept` (via `onlyHost`/`restrict(_params.host)`-style checks used throughout `IntentsBase`/`ExtrinsicIntents`), and `_params.dispatcher` is used to route escrow releases and cross-chain message dispatch for every order placed against this gateway (`params()` is read directly for outbound dispatch and fill settlement, and `host()` is also used to authorize `onAccept` governance actions like `SetHostParam`/`Withdraw`/`UpgradeContract` in the sibling `HostManager`/`SimplexPaymaster`/`IntentGatewayV2` `onAccept` handlers). By front-running `initialize` with attacker-controlled `host`/`dispatcher` addresses, the attacker becomes the sole entity capable of impersonating "the host" for that gateway instance, letting them:
- Call `onAccept` themselves through their own malicious "host" contract to trigger privileged administrative code paths meant only for the genuine Hyperbridge coprocessor.
- Have any user funds later escrowed against this (now attacker-configured) gateway routed/dispatched through an attacker-controlled `dispatcher`, since order settlement and escrow release logic trusts `_params.dispatcher`/`_params.host` unconditionally after initialization.

This matches the required impact classes: unauthorized execution and loss of escrowed funds via a manipulated trust configuration, not merely a front-run for its own sake — the front-run is the *vector*, but the resulting state (permanently attacker-controlled host/dispatcher until redeploy) is the actual fund-custody vulnerability.

### Likelihood Explanation
This requires only an unprivileged attacker watching a public mempool for the Tron deployment transaction and submitting a normal `initialize` call with higher gas/priority — no compromised relayer, prover, or admin key is needed, and no malicious peer behavior is assumed. Because the vulnerability is confined to the non-atomic Tron migration path (the EVM/foundry `ERC1967Proxy` deploy path is not exploitable, since `initData` is bundled atomically), the exposure window exists specifically for any Tron deployment/redeployment of `IntentGatewayV2`, including implementation redeploys via `DeployIntentGatewayImpl.s.sol` if ever adapted to a similar two-step flow.

### Recommendation
- Restrict `initialize()` to a known deployer/admin address (e.g., `require(msg.sender == _owner)`), mirroring the pattern already used in `HostManager.setIsmpHost` and `EvmHost.initialize`, so a stray front-run call cannot succeed even if deployment and initialization are non-atomic.
- Update the Tron migration (`evm/tron/migrations/2_deploy_ismp.js`) to make deployment and initialization atomic (e.g., deploy behind a proxy whose constructor embeds the init call, as done in the EVM script), removing the exposed window entirely.

### Proof of Concept
1. Attacker monitors the Tron network for the `deployer.deploy(IntentGatewayV2, admin)` transaction from `evm/tron/migrations/2_deploy_ismp.js:216-221`.
2. As soon as the `IntentGatewayV2` contract address is known (before the script's subsequent `initialize` call at lines 224-239 lands, given the explicit `sleep(BLOCK_TIME)` wait), the attacker submits:
   `intentGateway.initialize(Params({host: attackerHost, dispatcher: attackerDispatcher, ...}), peerChains)`.
3. Because `initialize` (evm/src/apps/IntentGatewayV2.sol:104) has no `msg.sender` check, this succeeds and consumes the one-time `initializer` slot.
4. The legitimate migration's later `initialize` call reverts with `InvalidInitialization`, confirming compromise only after the fact.
5. The attacker's `attackerHost`/`attackerDispatcher` contracts are now the permanent trust anchors for this gateway instance, letting the attacker call `onAccept` as "the host" and control dispatch/settlement for any funds subsequently escrowed against it.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L64-75)
```text
    /// @dev Privileged admin for future upgrade-gated actions (e.g. pausing). Immutable, so it must
    /// be identical across chains or the deterministic proxy address diverges. Does not gate
    /// `initialize`; atomic CREATE2 deployment already binds the init data to the canonical address.
    address public immutable _owner;

    /// @dev Sets the EIP-712 domain ("IntentGateway", "2"), records the admin, and locks this raw
    /// implementation against direct initialization.
    /// @param owner The privileged admin address.
    constructor(address owner) EIP712("IntentGateway", "2") {
        _owner = owner;
        _disableInitializers();
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L104-115)
```text
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

**File:** evm/script/DeployIntentGateway.s.sol (L46-60)
```text
        bytes memory initData = abi.encodeCall(
            IntentGatewayV2.initialize,
            (
                Params({
                    host: HOST_ADDRESS,
                    dispatcher: config.get("CALL_DISPATCHER").toAddress(),
                    solverSelection: config.get("7702").toBool(),
                    surplusShareBps: 5_000, // 50%
                    protocolFeeBps: 30, // 0.3%
                    priceOracle: priceOracle
                }),
                peerChains
            )
        );
        ERC1967Proxy proxy = new ERC1967Proxy{salt: salt}(address(implementation), initData);
```

**File:** evm/tron/migrations/2_deploy_ismp.js (L216-239)
```javascript
    //  7. Deploy IntentGatewayV2
    // ═════════════════════════════════════════════════════════════════════
    console.log("→ Deploying IntentGatewayV2 ...");
    await deployer.deploy(IntentGatewayV2, admin);
    const intentGateway = await IntentGatewayV2.deployed();
    console.log("  ✓ IntentGatewayV2:", intentGateway.address);

    // ═════════════════════════════════════════════════════════════════════
    //  7a. Initialize IntentGatewayV2 parameters
    // ═════════════════════════════════════════════════════════════════════
    console.log("→ Waiting for block confirmation ...");
    await sleep(BLOCK_TIME);
    console.log("→ Initializing IntentGatewayV2 params ...");

    // Params struct — field order must match the Solidity struct definition
    const intentParams = [
        tronHost.address, // host
        callDispatcher.address, // dispatcher
        solverSelection, // solverSelection
        surplusShareBps, // surplusShareBps
        protocolFeeBps, // protocolFeeBps
        priceOracle, // priceOracle
    ];

```
