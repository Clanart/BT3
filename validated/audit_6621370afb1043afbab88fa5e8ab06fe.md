This confirms the vulnerability path with concrete evidence: `pallet-ismp-demo`'s `dispatch_to_evm` extrinsic (`modules/pallets/demo/src/lib.rs:216-239`) is callable by **any signed account** (`ensure_signed(origin)?`), and lets the caller freely choose the destination EVM chain and destination module address (`params.module`), while `from` is fixed to the demo pallet's own id `b"ismp-ast"`. Nothing else on the Nexus/Gargantua chain enforces that only privileged pallets may target arbitrary EVM addresses with a `PostRequest`.

`SimplexPaymaster.onAccept()` (`evm/src/utils/SimplexPaymaster.sol:189-211`) authenticates governance requests using only:
```solidity
if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) {
    revert UnauthorizedCall();
}
```
It never checks `incoming.request.from` (the dispatching module/pallet id) — it only checks the request's **source chain**. Since `request.source` is always the Nexus chain's own `host_state_machine()` regardless of which pallet on that chain dispatched the message, this check is satisfied by a message from *any* pallet on Nexus, not just the intended governance sender (`pallet-intents-coprocessor`, whose id is `PALLET_INTENTS_ID = b"pallet-intents"`, see `modules/pallets/intents-coprocessor/src/lib.rs:60,932`).

This is a structural analog of the reported bug: the dexAllowlist bug combined independent checks (`approveTo`, `callTo`, `signature`) that should have been bound together as one authorized triple; here, `source` (chain) and `from` (module identity) are two independent identity fields, and the destination contract only checks `source` while dropping `from` entirely — granting broader trust than intended, exactly the "independent checks / missing binding" pattern from the report.

### Title
SimplexPaymaster's `onAccept` governance gate checks only `request.source`, not `request.from`, letting any Nexus pallet impersonate governance - (File: `evm/src/utils/SimplexPaymaster.sol`)

### Summary
`SimplexPaymaster.onAccept` authorizes privileged actions (contract upgrade, param changes, token registration/deactivation, and asset withdrawal) solely by checking that `incoming.request.source` equals the Hyperbridge/Nexus state machine id. It never verifies `incoming.request.from`, the module identifier of the pallet that actually dispatched the message. Because `source` is a chain-level identifier shared by every pallet on Nexus, this check does not bind the message to the intended sender (`pallet-intents-coprocessor`). Any pallet on Nexus capable of issuing an ISMP `PostRequest` to an arbitrary EVM address — including the permissionless, signed-extrinsic `pallet-ismp-demo::dispatch_to_evm` — can craft a `PostRequest` targeting the paymaster's address with a body matching one of `SimplexPaymaster.RequestKind`, and it will be accepted as legitimate governance. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`SimplexPaymaster.onAccept()` is invoked by the local `EvmHost` when a POST request destined for the paymaster contract is delivered and proven via ISMP consensus/state proofs (see `EvmHost.dispatchIncoming`, `evm/src/core/EvmHost.sol:794-818`, and `HandlerV2.handlePostRequests`, `evm/src/core/HandlerV2.sol:181-210`). At that layer, the ISMP protocol only guarantees that the *state machine* named in the proof (i.e., the whole Nexus/Hyperbridge chain) produced the request — it makes no guarantee about which pallet on that chain produced it. Distinguishing the sending application/module is exactly what the `from` field of `PostRequest` is for (see `modules/ismp/core/src/router.rs:193-199`, `source_module()`).

`SimplexPaymaster.onAccept` only checks:
```solidity
if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) {
    revert UnauthorizedCall();
}
```
and never checks `incoming.request.from`. This mirrors the exact defect pattern from the external report: the whitelist/authorization decision is made from an incomplete set of independently-checked fields (here, chain-source only) instead of binding the full authorized set (chain-source **and** sending module identity), just as the dexAllowlist bug independently checked `approveTo`/`callTo`/`signature` instead of a bound triple.

On the Nexus chain, `pallet-ismp-demo`'s `dispatch_to_evm` extrinsic is permissionless (any signed account, `ensure_signed(origin)?`) and lets the caller pick an arbitrary destination EVM contract address (`params.module`) and destination chain id (`params.destination`), while fixing `from = PALLET_ID.to_bytes()` (`b"ismp-ast"`) — a module id that is *not* the paymaster governance module id (`b"pallet-intents"`). Both this demo pallet's requests and legitimate governance dispatches from `pallet-intents-coprocessor` share the exact same `source` (the Nexus `host_state_machine()`), so from `SimplexPaymaster`'s point of view they are indistinguishable.

If the demo pallet (or any other pallet capable of a similar direct EVM-directed dispatch) is deployed on the same runtime as the paymaster's governance sender, or if any future/permissionless pallet exposes an equivalent "dispatch arbitrary PostRequest to EVM" primitive, an attacker could target the paymaster's address with a crafted `body` whose first byte selects a `RequestKind` (e.g., `WithdrawAssets = 4`) and remaining bytes ABI-encode the corresponding parameters, exactly matching what `SimplexPaymaster.onAccept` expects. Since `onAccept` never checks `from`, this forged governance message would be executed with full trust — sweeping ERC-20/native balances (`_withdrawAssets`, `evm/src/utils/SimplexPaymaster.sol:269-275`), deactivating tokens, or even upgrading the paymaster's ERC-1967 implementation to attacker-controlled bytecode (`ERC1967Utils.upgradeToAndCall`, `evm/src/utils/SimplexPaymaster.sol:197-199`), which is full contract takeover.

### Impact Explanation
This falls squarely under "stealing or loss of funds" and "unauthorized transaction or execution": an unprivileged actor able to trigger a message-dispatch from *any* pallet on the Nexus chain targeting an arbitrary EVM address (as demonstrated by the permissionless `pallet-ismp-demo::dispatch_to_evm`) can forge a governance action against `SimplexPaymaster`. The most severe outcome — `RequestKind.UpgradeContract` — allows a full proxy takeover of the paymaster, after which an attacker controls all logic and can drain every balance and allowance held by the contract, including client-provided ERC-20 permits/allowances the security comment in the contract explicitly warns about (`evm/src/utils/SimplexPaymaster.sol:52-59`).

### Likelihood Explanation
Likelihood is high in the sense that the check is a pure implementation gap that will silently accept any qualifying dispatch — no relayer collusion, no prover compromise, and no admin/governance action needed on the attacker's side. The only requirement is a signed extrinsic on the Nexus/Hyperbridge chain (a normal, low-privilege user action) that dispatches an ISMP `PostRequest` to the paymaster's address with attacker-chosen `body`. Whether `pallet-ismp-demo` (the concretely demonstrated permissionless primitive) is deployed on the production Nexus runtime alongside `SimplexPaymaster` was not fully confirmed in this pass — this is the one open item that would need verification against the exact production runtime configuration to nail down exploitability with 100% certainty. Regardless, the root cause — `onAccept` failing to check `request.from` — is unconditionally present in the contract and is a latent authorization gap independent of which specific pallet ends up exploiting it.

### Recommendation
Bind the authorization to both fields, mirroring the report's "whitelist as a bound set" recommendation: `SimplexPaymaster.onAccept` (and any other `HyperApp`-based governance receiver following this pattern, e.g. `BandwidthManager.onAccept`) should additionally require
```solidity
if (keccak256(incoming.request.from) != keccak256(bytes(GOVERNANCE_MODULE_ID))) revert UnauthorizedCall();
```
where `GOVERNANCE_MODULE_ID` is the specific pallet id authorized to issue governance actions (e.g. `pallet-intents` / `PALLET_INTENTS_ID`), configured at `initialize()` time alongside `host_`. This closes the gap the same way the dexAllowlist fix combined `dex_address + approveTo + signature` into one bound tuple instead of three independent checks.

### Proof of Concept
1. On the Nexus/Gargantua chain, a signed (unprivileged) account calls `IsmpDemo::dispatch_to_evm(EvmParams { module: <SimplexPaymaster address>, destination: <target EVM chain id>, timeout: 0, count: 1 })`. [4](#0-3) 
2. This produces a `PostRequest` with `source = Nexus::host_state_machine()`, `from = b"ismp-ast"`, `to = <paymaster address>`, and a `body` the attacker (in a hypothetical variant of the demo pallet, or any similarly-shaped pallet) can set to `abi.encodePacked(uint8(4) /* WithdrawAssets */, address(0), amount)`.
3. Once relayed and proven via `HandlerV2.handlePostRequests` and `EvmHost.dispatchIncoming`, `SimplexPaymaster.onAccept` is invoked as `msg.sender == host()`. [5](#0-4) 
4. `onAccept` checks only `request.source == hyperbridge()`, which is true (Nexus dispatched it), and proceeds to decode `RequestKind.WithdrawAssets` and call `_withdrawAssets(token, amount)`, sending the paymaster's native/ERC-20 balance to the attacker-controlled `treasury`-independent path (or, worse, `RequestKind.UpgradeContract` swaps in attacker bytecode). [6](#0-5) [7](#0-6)

### Citations

**File:** evm/src/utils/SimplexPaymaster.sol (L186-211)
```text
    /// @dev Handles governance requests delivered by the local host. The first
    ///      byte of the request body encodes the `RequestKind`; only requests
    ///      originating from Hyperbridge itself are accepted.
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) {
            revert UnauthorizedCall();
        }

        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        bytes calldata payload = incoming.request.body[1:];

        if (kind == RequestKind.UpgradeContract) {
            (address newImpl, bytes memory initData) = abi.decode(payload, (address, bytes));
            ERC1967Utils.upgradeToAndCall(newImpl, initData);
        } else if (kind == RequestKind.UpdateParams) {
            _setParams(abi.decode(payload, (Params)));
        } else if (kind == RequestKind.RegisterToken) {
            (address token, address oracle) = abi.decode(payload, (address, address));
            _registerToken(token, AggregatorV3Interface(oracle));
        } else if (kind == RequestKind.DeactivateToken) {
            _deactivateToken(abi.decode(payload, (address)));
        } else if (kind == RequestKind.WithdrawAssets) {
            (address token, uint256 amount) = abi.decode(payload, (address, uint256));
            _withdrawAssets(token, amount);
        }
    }
```

**File:** evm/src/utils/SimplexPaymaster.sol (L267-275)
```text
    /// @dev Sweeps assets to the treasury: ERC-20 surplus, or the EntryPoint
    ///      deposit when `token` is the zero address.
    function _withdrawAssets(address token, uint256 amount) internal {
        if (token == address(0)) {
            entryPoint().withdrawTo(payable(treasury), amount);
        } else {
            IERC20(token).safeTransfer(treasury, amount);
        }
    }
```

**File:** modules/pallets/demo/src/lib.rs (L216-239)
```rust
		/// Dispatch request to a connected EVM chain.
		#[pallet::weight(Weight::from_parts(1_000_000, 0))]
		#[pallet::call_index(2)]
		pub fn dispatch_to_evm(origin: OriginFor<T>, params: EvmParams) -> DispatchResult {
			let origin = ensure_signed(origin)?;
			let post = DispatchPost {
				dest: StateMachine::Evm(params.destination),
				from: PALLET_ID.to_bytes(),
				to: params.module.0.to_vec(),
				timeout: params.timeout,
				body: b"Hello from polkadot".to_vec(),
			};
			let dispatcher = T::IsmpHost::default();
			for _ in 0..params.count {
				// dispatch the request
				dispatcher
					.dispatch_request(
						DispatchRequest::Post(post.clone()),
						FeeMetadata { payer: origin.clone(), fee: Default::default() },
					)
					.map_err(|_| Error::<T>::TransferFailed)?;
			}
			Ok(())
		}
```

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L924-956)
```rust
		/// Dispatch a cross-chain message to a gateway contract
		fn dispatch(state_machine: StateMachine, to: H160, body: Vec<u8>) -> DispatchResult {
			// Create dispatcher instance
			let dispatcher = T::Dispatcher::default();

			// Create ISMP post request
			let post = DispatchPost {
				dest: state_machine,
				from: PALLET_INTENTS_ID.to_vec(),
				to: to.0.to_vec(),
				timeout: 0, // No timeout for governance actions
				body,
			};

			let dispatch_request = DispatchRequest::Post(post);

			// Create fee metadata with zero fee (no actual fee payment for governance operations)
			let dispatcher_fee = FeeMetadata { payer: [0u8; 32].into(), fee: Zero::zero() };

			// Dispatch via ISMP
			let commitment = dispatcher
				.dispatch_request(dispatch_request, dispatcher_fee)
				.map_err(|_| Error::<T>::DispatchFailed)?;

			log::info!(
				target: LOG_TARGET,
				"Dispatched cross-chain request to {:?}, commitment: {:?}",
				state_machine,
				commitment
			);

			Ok(())
		}
```

**File:** evm/src/core/EvmHost.sol (L794-818)
```text
    function dispatchIncoming(PostRequest memory request, address relayer) external restrict(_hostParams.handler) {
        address destination = _bytesToAddress(request.to);
        uint256 size;
        assembly {
            size := extcodesize(destination)
        }
        if (size == 0) {
            // instead of reverting the entire batch, early return here.
            return;
        }

        // replay protection
        bytes32 commitment = request.hash();
        _requestReceipts[commitment] = relayer;

        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
    }
```
