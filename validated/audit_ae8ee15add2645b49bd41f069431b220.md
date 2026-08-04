Based on my investigation, the governance-gated upgrade path (`ERC1967Utils.upgradeToAndCall` in `IntentGatewayV2.onAccept` / `SimplexPaymaster.onAccept`) is not exploitable by an unprivileged actor — it requires the `intents-coprocessor` pallet's `GovernanceOrigin` to dispatch an `UpgradeContract` request [1](#0-0) , so per the impact gate that path is excluded.

However, I found a real, unprivileged analog of the "storage layout corruption" bug class: the cross-chain fill-proof mechanism depends on a **hardcoded magic storage slot** that is duplicated across two independently maintained contract copies with materially different storage layouts and no shared layout test.

### Title
Hardcoded `_filled` storage-slot constant duplicated across divergent IntentGatewayV2 implementations risks false "unfilled" state proof and double-settlement - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The IntentGatewayV2 cross-chain cancellation flow proves an order was never filled by reading the `_filled` mapping's storage slot via a Hyperbridge GET request and comparing it against a **hardcoded slot constant**, `FILLED_SLOT_BIG_ENDIAN_BYTES = 0x02` [2](#0-1) , used in `_calculateCommitmentSlotHash` [3](#0-2)  and consumed by `onGetResponse` to gate refunds [4](#0-3) .

This exact same slot constant is re-declared verbatim in a second, independently maintained implementation, `evm/tron/contracts/apps/IntentGatewayV2.sol` [5](#0-4) , but that contract's inheritance and storage-variable set diverge from the mainline version: it inherits `HyperApp, EIP712` instead of `IntentsBase`, declares an extra `_admin` storage variable not present in `IntentsBase` [6](#0-5) , omits the mainline's trailing `_paused` slot-preservation variable, and even defines a different `RequestKind` enum missing `RefundEscrow`/`UpgradeContract` variants [7](#0-6) . This is precisely the storage-layout drift class described in the external report: independent copies of "the same" logic silently diverging in variable order/composition while a security-critical magic value (the slot number used for cross-chain proof verification) is copy-pasted unchanged.

### Finding Description
The mainline EVM contract is explicit that slot correctness is safety-critical and pins it with a dedicated regression test, `testFilledMappingStaysAtSlotTwo`, which loads storage directly via `vm.load` and asserts `_filled` sits at slot 2 [8](#0-7) . No equivalent test exists for the Tron implementation. Because the Tron contract's declared inheritance (`HyperApp, EIP712`) and variable set (extra `_admin`, no `_paused`) differ from the mainline (`IntentsBase`, no `_admin`, trailing `_paused`), there is no guarantee — and no test enforcing — that `_filled` actually occupies slot 2 in the deployed Tron bytecode. The off-chain/relayer tooling that builds the GET-request storage proof also hardcodes the same `mappingSlot = 2n` assumption independently in the SDK [9](#0-8) , meaning the wrong-slot assumption is baked into multiple layers rather than caught by any single storage-layout check.

### Impact Explanation
If `_filled` does not actually reside at slot 2 in a deployed IntentGatewayV2 instance (mainline drift, Tron variant, or any future fork/version that changes variable order), the storage proof constructed for that slot will resolve to an unrelated storage word — most likely always empty for a freshly laid-out contract. `onGetResponse` treats an empty proof value as "not filled" and unconditionally proceeds to refund the escrowed input to the original user [4](#0-3) , even when the order was already filled and the solver already received the destination-side payout. This is a direct double-settlement / duplicate-fund-release: the solver is paid on the destination chain and the user is separately refunded the same escrow on the source chain, resulting in fund loss to the protocol.

### Likelihood Explanation
This requires no privileged actor: any user who places and has their own order filled can subsequently call the ordinary `_cancelFromSource` path to request the GET proof and attempt a refund; no relayer/prover misbehavior is needed beyond the normal (trusted) proof-delivery infrastructure the protocol already relies on for legitimate cancellations. The likelihood hinges entirely on whether the actual deployed storage slot for `_filled` matches the hardcoded constant — I could not fully confirm the exact linear-storage footprint of the imported `HyperApp` base (`sdk/packages/core/contracts/apps/HyperApp.sol`) within this session's tool budget, so I cannot state with certainty that the Tron layout is currently mismatched. What is concretely verified is the divergence in inheritance/variable composition between the two copies and the complete absence of a layout-pinning test for the Tron variant, which is the exact "no verification before upgrade/fork" failure mode the external report warns about.

### Recommendation
Add an explicit `vm.load`-based storage-slot regression test for every deployed IntentGatewayV2 variant (mainline and Tron), matching `testFilledMappingStaysAtSlotTwo`; alternatively, replace the hardcoded slot constant with a slot computed via inline assembly (`_filled.slot`) at compile time in each variant so the proof-target constant can never silently diverge from the actual layout, and add CI storage-layout diffing (e.g., `forge inspect storage-layout`) across all IntentGatewayV2 forks to catch future drift.

### Proof of Concept
1. Deploy `evm/tron/contracts/apps/IntentGatewayV2.sol` and inspect its actual storage layout (e.g., `forge inspect storage-layout` or on-chain `vm.load` probing as done in `testFilledMappingStaysAtSlotTwo`) to determine the real slot of `_filled`.
2. If it differs from `0x02`, place and fill a same-chain-originated cross-chain order so `_filled[commitment]` is set to the solver's address.
3. Trigger `_cancelFromSource` from the user account; the relayer constructs the GET storage proof for slot 2 (per `_calculateCommitmentSlotHash`/SDK `mappingSlot = 2n`), which resolves to an empty/unrelated slot on the mismatched contract.
4. `onGetResponse` sees `value.length == 0`, treats the order as unfilled, and refunds the user's escrow — even though the solver was already paid on the destination chain, resulting in double payment for the same order.

### Citations

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L637-646)
```rust
		#[pallet::call_index(7)]
		#[pallet::weight(T::WeightInfo::upgrade_gateway())]
		pub fn upgrade_gateway(
			origin: OriginFor<T>,
			state_machine: StateMachine,
			new_impl: H160,
			init_data: Vec<u8>,
		) -> DispatchResult {
			T::GovernanceOrigin::ensure_origin(origin)?;

```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L68-73)
```text
    /**
     * @dev Big-endian encoding of storage slot 2 (the `_filled` mapping slot).
     * Used to construct storage proof keys for cross-chain cancel verification.
     */
    bytes32 constant FILLED_SLOT_BIG_ENDIAN_BYTES =
        hex"0000000000000000000000000000000000000000000000000000000000000002";
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L364-373)
```text
    /**
     * @dev Computes the storage slot hash for a given commitment in the `_filled` mapping.
     * This is used to construct storage proof keys for cross-chain cancellation verification
     * via Hyperbridge GET requests.
     * @param commitment The order commitment hash.
     * @return The ABI-encoded storage slot hash.
     */
    function _calculateCommitmentSlotHash(bytes32 commitment) internal pure returns (bytes memory) {
        return abi.encodePacked(keccak256(abi.encodePacked(commitment, FILLED_SLOT_BIG_ENDIAN_BYTES)));
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L319-324)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L66-77)
```text
    enum RequestKind {
        /// @dev Identifies a request for redeeming an escrow.
        RedeemEscrow,
        /// @dev Identifies a request for recording new contract deployments
        NewDeployment,
        /// @dev Identifies a request for updating parameters.
        UpdateParams,
        /// @dev Identifies a request for sweeping accumulated dust
        SweepDust,
        /// @dev Identifies a request for refunding an escrow (cancellation from destination chain)
        RefundEscrow
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L85-90)
```text
    /**
     * @notice Constant representing a filled slot in big endian format
     * @dev Hex value 0x06 padded with leading zeros to fill 32 bytes
     */
    bytes32 constant FILLED_SLOT_BIG_ENDIAN_BYTES =
        hex"0000000000000000000000000000000000000000000000000000000000000002";
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L111-115)
```text
    /**
     * @dev Address of the admin, which can initialize the contract.
     * The admin is reset to the zero address after initialization.
     */
    address private _admin;
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L3805-3815)
```text
    function testFilledMappingStaysAtSlotTwo() public {
        (bytes32 filledCommitment,,,) = _seedUpgradeState();

        // _filled is `mapping(bytes32 => address)` declared at storage slot 2. The cross-chain
        // cancel proof (FILLED_SLOT_BIG_ENDIAN_BYTES) depends on this exact slot.
        bytes32 slot = keccak256(abi.encode(filledCommitment, uint256(2)));
        address filledFromSlot = address(uint160(uint256(vm.load(address(intentGateway), slot))));

        assertEq(filledFromSlot, filler, "_filled must occupy storage slot 2");
        assertEq(filledFromSlot, intentGateway._filled(filledCommitment), "slot-2 read matches getter");
    }
```

**File:** sdk/packages/simplex/src/tests/strategies/fx.mainnet.test.ts (L2254-2266)
```typescript
async function checkIfOrderFilled(
	commitment: HexString,
	client: PublicClient,
	intentGatewayV2Address: HexString,
): Promise<boolean> {
	try {
		const mappingSlot = 2n
		const slot = keccak256(encodePacked(["bytes32", "uint256"], [commitment, mappingSlot]))
		const filledStatus = await client.getStorageAt({
			address: intentGatewayV2Address,
			slot,
		})
		return filledStatus !== "0x0000000000000000000000000000000000000000000000000000000000000000"
```
