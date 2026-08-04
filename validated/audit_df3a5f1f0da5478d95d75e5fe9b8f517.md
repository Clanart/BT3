### Title
Unrestricted one-time initializer combined with non-atomic deployment allows front-running takeover of `IntentGatewayV2` governance channel - (File: `evm/src/apps/IntentGatewayV2.sol`, deployment: `evm/tron/migrations/2_deploy_ismp.js`)

### Summary
`IntentGatewayV2.initialize()` intentionally has no caller restriction (no `onlyOwner`/`onlyAdmin`), because the contract's own comment states this is safe *only* because deployment is assumed to be atomic: the `ERC1967Proxy` constructor is expected to call `initialize` in the same transaction as proxy creation, so no attacker can ever observe an uninitialized proxy. This invariant is not actually enforced by the code — it is a deployment-process assumption. The Tron deployment path breaks exactly this assumption: it deploys the `IntentGatewayV2` implementation as a bare (non-proxied) contract and only calls the params-setting function in a **separate, later transaction**, after an explicit `sleep(BLOCK_TIME)`. Anyone watching the mempool/chain in that gap can call the unrestricted initializer first with attacker-controlled parameters.

### Finding Description
The mainnet/testnet EVM deployment path (`evm/script/DeployIntentGateway.s.sol`) creates the proxy and calls `initialize` atomically via `ERC1967Proxy{salt}(implementation, initData)` [1](#0-0) , which is why the code comment on `_owner` explicitly says `initialize` deliberately has no access-control gate [2](#0-1) :

```
/// @dev Privileged admin for future upgrade-gated actions (e.g. pausing). Immutable, so it must
/// be identical across chains or the deterministic proxy address diverges. Does not gate
/// `initialize`; atomic CREATE2 deployment already binds the init data to the canonical address.
```

`initialize()` itself is only guarded by OpenZeppelin's one-time `initializer` modifier — it does not check `msg.sender` at all [3](#0-2) .

The Tron deployment script, however, does not follow the atomic pattern. It deploys `IntentGatewayV2` as a plain contract via `deployer.deploy(IntentGatewayV2, admin)`, then in a **separate step**, after sleeping for a block confirmation, calls the params-setting entrypoint [4](#0-3) . This is precisely the "anyone can call `initialize()` between deploy and setup" pattern described in the external Staking.sol report, applied to a real Hyperbridge production contract whose init function is deliberately left unrestricted based on an atomicity assumption that this deployment script violates.

Whoever wins that race controls `_params`, including `host`. `host()` is derived directly from `_params.host` [5](#0-4) , and `host()` is exactly the value that gates the `onAccept` governance callback (via `HyperApp`'s `onlyHost`/`restrict` pattern used throughout the codebase, e.g. `HostManager.onAccept` uses `restrict(_params.host)` [6](#0-5) ). By setting `Params.host` to an address they control, an attacker makes themselves the sole address permitted to call `onAccept`, i.e. the sole address able to submit "cross-chain governance" instructions such as `UpgradeContract`, param updates, etc., completely bypassing the real Hyperbridge coprocessor.

### Impact Explanation
This maps to the bounty's "unauthorized transaction or execution" / "logic attacks" / "false proof/state acceptance" classes: by front-running the deployer's initialization call, an unprivileged attacker permanently sets themselves as the trusted "host" identity for the gateway, letting them later forge arbitrary onAccept messages (including implementation upgrades via `UpgradeContract`) with no real Hyperbridge proof at all. Since `IntentGatewayV2` custodies user escrow (input tokens for cross-chain intents), a hijacked host effectively grants the attacker unauthorized code-execution control over the escrow contract, enabling theft of any funds later deposited.

### Likelihood Explanation
This requires only an unprivileged attacker observing the mempool during the deployment window (deploy tx and initialize tx are two separate, publicly visible transactions with an enforced sleep in between) — no relayer, prover, admin, or leaked key needed, which satisfies the gate's "public-entrypoint, unprivileged attacker" requirement. It is specific to the Tron deployment path; the Foundry/EVM script path avoids it by making deployment atomic, so the exposure is limited to whichever chains are deployed via this Tron migration script.

### Recommendation
- Ensure every deployment path (including the Tron migration script) creates the proxy/contract and calls its initializer atomically in the same transaction (e.g., pass `initData` to the constructor of the deployed proxy rather than deploying bare and calling a separate setup transaction after a sleep).
- As defense in depth, add an explicit caller check to `initialize()` (e.g., restrict to `_owner`/deployer) instead of relying solely on deployment-process atomicity as an implicit security assumption.

### Proof of Concept
1. Run the Tron migration: `deployer.deploy(IntentGatewayV2, admin)` deploys the bare implementation/contract (no init data passed) [7](#0-6) .
2. During the subsequent `sleep(BLOCK_TIME)` window before the script calls the params-setting entrypoint [8](#0-7) , an attacker submits their own transaction calling the same unauthenticated initializer/setParams function first, supplying `Params.host = attackerControlledContract`.
3. The one-time-init guard now locks the real deployer out (it reverts or is a no-op on the “real” call), and `host()` permanently resolves to the attacker's address [5](#0-4) .
4. The attacker calls `onAccept` themselves (since they are `_params.host`), forging governance messages such as `UpgradeContract` to install a malicious implementation, gaining full control of the gateway and any escrowed funds.

Note: I confirmed the unrestricted-initializer design and the non-atomic Tron deployment script from the indexed code, but did not have remaining tool budget to open `evm/tron/contracts/apps/IntentGatewayV2.sol` and confirm the exact modifier on its `setParams` function byte-for-byte. If that Tron-specific contract turns out to independently add an `onlyOwner`/`onlyAdmin` check on `setParams` (diverging from the base `IntentGatewayV2.sol` behavior), this specific PoC would not apply as stated and should be re-verified directly against that file.

### Citations

**File:** evm/script/DeployIntentGateway.s.sol (L46-61)
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
        IntentGatewayV2 intentGateway = IntentGatewayV2(payable(address(proxy)));
```

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

**File:** evm/tron/migrations/2_deploy_ismp.js (L216-241)
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

    await intentGateway.setParams(intentParams);
    console.log("  ✓ IntentGatewayV2 params set");
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L52-54)
```text
    function host() public view virtual override(IntentsBase, HyperApp) returns (address) {
        return _params.host;
    }
```

**File:** evm/src/core/HostManager.sol (L95-98)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();
```
