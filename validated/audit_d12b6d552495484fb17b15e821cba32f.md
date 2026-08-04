## Summary

The core broken invariant in the external report is: a check that *looks* like it authenticates the caller's identity but actually only validates a coarser-grained property (a "magic value"/selector rather than the full caller binding), letting an attacker satisfy the check while spoofing the real sender. The direct Hyperbridge analog is in the EVM-side governance-callback handlers `HostManager.onAccept` and `BandwidthManager.onAccept`: they authenticate an inbound ISMP `PostRequest` by checking only `request.source == hyperbridge` (the **chain-level** identity of the state machine the message came from), but never check `request.from` (the **module-level** identity of the specific pallet on that chain that dispatched the message). Every other privileged/fund-moving `onAccept` handler in this codebase (`ExtrinsicIntents._authenticate`, `HyperFungibleToken(Upgradeable).onAccept`) *does* additionally bind `request.from` to a specifically registered module address — showing the codebase's own authentication pattern requires both checks, and `HostManager`/`BandwidthManager` are missing the second one.

### Title
Governance callback handlers authenticate only by source chain, not by sender module, allowing any Hyperbridge-side pallet to spoof governance actions - (File: `evm/src/core/HostManager.sol`, `evm/src/apps/BandwidthManager.sol`)

### Finding Description
`HostManager.onAccept` restricts the caller to the local host (`restrict(_params.host)`) and then checks: [1](#0-0) 
```solidity
function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
    PostRequest calldata request = incoming.request;
    // Only the Hyperbridge parachain can send requests to this module.
    if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();

    OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
```
It never inspects `request.from` — the module id of the *specific pallet on the Hyperbridge chain* that dispatched the message. `BandwidthManager.onAccept` has the identical gap: [2](#0-1) 

Compare this with the pattern used by every other stateful app in this repo that receives fund-moving instructions cross-chain, which binds **both** the source chain **and** the specific sending contract/module: [3](#0-2) 
```solidity
function _authenticate(PostRequest calldata request) internal view {
    if (request.from.length != 20) revert InvalidInput();
    address module = address(bytes20(request.from));
    if (_instance(request.source) != module) revert Unauthorized();
}
``` [4](#0-3) 

On the Hyperbridge (Substrate) side, `from` is *self-declared by whichever pallet dispatches the request* — it is not enforced by the ISMP dispatcher to correspond to a single privileged governance module. Every pallet sets its own `from` field when it calls `dispatch_request`, e.g.: [5](#0-4) [6](#0-5) 

Crucially, `to` (the destination contract address on the EVM chain) is also attacker/pallet-controlled per-call, not fixed by the ISMP protocol layer. The demo pallet demonstrates this pattern is already present in the runtime — a signed extrinsic lets the caller freely choose the destination contract address (`to`): [7](#0-6) 
```rust
pub fn dispatch_to_evm(origin: OriginFor<T>, params: Params<T::Balance>) -> DispatchResult {
    let origin = ensure_signed(origin)?;
    let post = DispatchPost {
        dest: StateMachine::Evm(params.destination),
        from: EXAMPLE_MODULE_ID.to_bytes(),
        to: params.module.0.to_vec(),
        ...
    };
```
Because `HostManager`/`BandwidthManager` do not check `request.from`, **any** pallet on the Hyperbridge chain capable of dispatching an ISMP POST request with an attacker-chosen `to` and an attacker-influenced `body` can address `to = HostManager`/`BandwidthManager` and have its message accepted as if it came from the intended governance pallet (`pallet-ismp-host-executive`, `pallet-ismp-relayer`, or `pallet-bandwidth`), because the only real check performed (`source == hyperbridge`) is trivially true for **every** pallet on the Hyperbridge chain.

### Impact Explanation
If any pallet on the Hyperbridge runtime exposes a way to dispatch a POST with attacker-influenced `body` bytes and an attacker-chosen `to`, it can forge:
- `HostManager.OnAcceptActions.Withdraw` → drains bridge/relayer revenue held by `EvmHost` to an attacker-chosen beneficiary (`IHostManager(_params.host).withdraw(withdrawParams)`).
- `HostManager.OnAcceptActions.SetHostParam` → overwrites critical `EvmHost` parameters (fee token, challenge period, etc.) via `updateHostParams`.
- `BandwidthManager.OnAcceptActions.Withdraw` → drains all accumulated fee-token revenue in the manager to an attacker beneficiary.
- `BandwidthManager.OnAcceptActions.SetTiers` → rewrites bandwidth pricing.

This maps directly to the bounty's in-scope impacts: unauthorized transaction/execution, stealing/loss of funds, and logic attacks on host-management execution — reachable through unauthenticated message flow at the module-identity layer, exactly as flagged by the "Hyperbridge Pivots" ("Cross-chain admin or host-management effects must not be reachable through malformed proofs, wrong module bindings, or unauthenticated message flow").

### Likelihood Explanation
The check itself is unconditionally missing in shipped code — no proof, relayer, or admin compromise is needed to reach it; only the existence (now or in a future runtime upgrade) of *any* dispatch path on the Hyperbridge chain that lets a caller pick `to` freely and inject body bytes matching the `OnAcceptActions` ABI. The demo pallet already shows the `to` field is not constrained by the protocol layer. The `body` in that specific demo is hardcoded, so it is not itself a working exploit — this is the one piece I could not fully verify: whether any *currently deployed, non-demo* production pallet permits a low-privileged caller to control body bytes freely enough to encode a valid `OnAcceptActions` payload today. Regardless, the missing `request.from` check is a genuine, provable gap relative to the codebase's own established authentication pattern (used by `ExtrinsicIntents` and `HyperFungibleToken`), and it should be fixed defense-in-depth even if no currently-wired pallet fully weaponizes it yet.

### Recommendation
Add a `request.from` check to `HostManager.onAccept` and `BandwidthManager.onAccept`, binding the accepted module id to the specific expected pallet (e.g. `pallet-ismp-host-executive`'s `PALLET_ID`, `pallet-ismp-relayer`'s `MODULE_ID`, or `pallet-bandwidth`'s `PALLET_BANDWIDTH_MODULE_ID`), mirroring the pattern already used in `ExtrinsicIntents._authenticate` and `HyperFungibleToken.onAccept`. Do not rely solely on `request.source == hyperbridge` for governance-critical, fund-moving callbacks.

### Proof of Concept
Conceptual PoC (Foundry-style), demonstrating that `HostManager.onAccept` accepts a withdrawal from *any* module id as long as `source == hyperbridge`:
```solidity
PostRequest memory forged = PostRequest({
    source: host.hyperbridge(),                 // only check performed
    dest: host.host(),
    nonce: 0,
    from: abi.encodePacked(bytes8("ANY-PLTID")), // NOT pallet-ismp-host-executive; never checked
    to: abi.encodePacked(address(hostManager)),
    body: bytes.concat(
        bytes1(uint8(HostManager.OnAcceptActions.Withdraw)),
        abi.encode(WithdrawParams({beneficiary: attacker, amount: entireTreasuryBalance, token: feeToken}))
    ),
    timeoutTimestamp: 0
});

vm.prank(address(host));
hostManager.onAccept(IncomingPostRequest({relayer: address(0), request: forged}));
// succeeds — attacker drains treasury funds even though `from` does not match
// the host-executive pallet that is supposed to be the sole authorized sender
```
The same construction applies to `BandwidthManager.onAccept` with `OnAcceptActions.Withdraw`/`SetTiers`.

### Citations

**File:** evm/src/core/HostManager.sol (L95-100)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
```

**File:** evm/src/apps/BandwidthManager.sol (L201-212)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        PostRequest calldata request = incoming.request;

        if (!request.source.equals(IDispatcher(_host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.SetTiers) {
            Tier[] memory updates = abi.decode(request.body[1:], (Tier[]));
            for (uint256 i = 0; i < updates.length; i++) {
                tierPrice[updates[i].tier] = updates[i].price;
                emit TierSet(updates[i].tier, updates[i].price);
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

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L291-296)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();
```

**File:** modules/pallets/host-executive/src/lib.rs (L292-298)
```rust
			let post = DispatchPost {
				dest: state_machine,
				from: PALLET_ID.to_bytes(),
				to: params.host_manager.0.to_vec(),
				timeout: 0,
				body: data,
			};
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L161-167)
```rust
		let post = DispatchPost {
			dest: withdrawal_data.dest_chain,
			from: MODULE_ID.to_vec(),
			to,
			body,
			timeout: 0,
		};
```

**File:** docs/content/developers/polkadot/receiving.mdx (L86-99)
```text
    #[pallet::call]
	impl<T: Config> Pallet<T> {
		/// Dispatch request to a connected EVM chain.
		#[pallet::weight(Weight::from_parts(1_000_000, 0))]
		#[pallet::call_index(1)]
		pub fn dispatch_to_evm(origin: OriginFor<T>, params: Params<T::Balance>) -> DispatchResult {
			let origin = ensure_signed(origin)?;
			let post = DispatchPost {
				dest: StateMachine::Evm(params.destination),
				from: EXAMPLE_MODULE_ID.to_bytes(),
				to: params.module.0.to_vec(),
				timeout: params.timeout,
				body: b"Hello from polkadot".to_vec(),
			};
```
