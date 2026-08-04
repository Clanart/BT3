## Summary

The external report's core broken invariant is: **a setter/authenticator that is supposed to gate a privileged state mutation accepts an unauthenticated or wrongly-scoped caller by default, because the guard degrades to a permissive fallback instead of rejecting.**

Scanning proof/request-handling, escrow, reward, and host-management setters across this repo (`EvmHost.sol`, `HostManager.sol`, `pallet_ismp_relayer`, `pallet_ismp_host_executive`, `BandwidthManager.sol`, `IntentsBase.sol`) shows that essentially all of them are locked down correctly with `restrict(...)`, `ensure_root`/`ensure_origin`, idempotency maps, and signature checks. The one place where the guard pattern **diverges from the safe reference implementation** is the Tron variant of the Intent Gateway's peer/module binding check.

## Finding Description

`evm/src/apps/intentsv2/IntentsBase.sol` resolves a cross-chain peer strictly: [1](#0-0) 

`_instance()` **reverts with `UnknownInstance`** if no peer gateway has been explicitly registered for a given `stateMachineId`. `ExtrinsicIntents.sol`'s `_authenticate()` relies on this strict resolution to gate `RedeemEscrow`/`RefundEscrow` withdrawals: [2](#0-1) 

The Tron port of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, replaces the strict-revert resolver with a **permissive fallback**: [3](#0-2) 

`instance()` returns `address(this)` instead of reverting when a source state machine has no registered peer. `authenticate()` (used identically to gate `RedeemEscrow`/`RefundEscrow` at line 622-625) then treats **any state machine that has not yet been explicitly bound via `NewDeployment` governance** as if its legitimate peer module were this contract's own address. This is the exact "setter/guard with no access control by default, only restrictable if the operator explicitly configures it" pattern from the external report, transplanted onto the module-identity binding that gates escrow release instead of onto an accessor setter.

## Impact Explanation

`authenticate()` protects the only two request kinds that move escrowed user/solver funds (`RedeemEscrow`, `withdraw(...)`). Because the peer-binding check silently defaults to "trust myself" for any not-yet-configured state machine, a `PostRequest` whose `source` is a state machine this deployment has not yet called `NewDeployment` for, and whose `from` equals this contract's own 20-byte address, passes `authenticate()` and reaches `withdraw()`, releasing escrowed tokens to an attacker-chosen beneficiary. This is a false module-binding acceptance leading directly to fund loss from escrow, matching the Hyperbridge pivot on binding module/app identity in request paths and not letting cross-chain effects be reachable through wrong module bindings.

## Likelihood Explanation

Exploitability is gated by the practical difficulty of getting `request.from` to equal this contract's raw 20-byte address on some other Hyperbridge-connected state machine. The main EVM `IntentGatewayV2` relies on deterministic CREATE2 addresses being identical across EVM chains by design, but Tron's runtime and address derivation are not covered by that guarantee, so the "same address means same trusted peer" assumption this fallback silently encodes does not hold for the Tron deployment. The realistic trigger is a timing gap: any state machine Hyperbridge adds support for **before** this Tron gateway's governance explicitly calls `_addDeployment`/`NewDeployment` for it is, by default, treated as bound to `address(this)` rather than rejected. This is a genuine logic defect distinct from the safe reference contract, but full weaponization additionally depends on either that registration-timing gap or an address-derivation coincidence, so likelihood is best rated low-to-moderate rather than trivially exercisable by any unprivileged attacker at any time.

## Recommendation

Make `instance()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` revert on an unregistered state machine, mirroring `_instance()` in `evm/src/apps/intentsv2/IntentsBase.sol`, so `authenticate()` never implicitly trusts an unconfigured source chain. Access control / module binding should default to closed (reject unknown peers) and only be opened once governance explicitly registers a peer, exactly as recommended in the openbrush report for setters.

## Proof of Concept

1. Hyperbridge admin has not yet called `NewDeployment` to bind state machine `X` to a peer gateway on the Tron `IntentGatewayV2` contract.
2. An attacker deploys (or otherwise controls) a contract on chain `X` whose address equals the raw 20 bytes of the Tron `IntentGatewayV2` contract's own address (or otherwise arranges `from` in an ISMP dispatch call to encode that value).
3. Attacker dispatches a `PostRequest` from chain `X` to the Tron gateway with `body = RequestKind.RedeemEscrow || WithdrawalRequest{commitment, tokens, beneficiary: attacker}` for a commitment corresponding to a real escrowed order.
4. Once delivered through the legitimate ISMP consensus/state-proof pipeline, `onAccept` → `authenticate(request)` calls `instance(X)`, which returns `address(this)` because `X` was never registered, matching `request.from` and passing authentication.
5. `withdraw(body, false)` releases the escrowed tokens to the attacker-controlled `beneficiary`. [4](#0-3)

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L358-362)
```text
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L63-67)
```text
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
    }
```

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
