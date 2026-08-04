Based on the code I was able to examine, I found a strong local analog: a **hardcoded storage-slot constant used to build a Hyperbridge storage-proof key**, exactly mirroring the external report's pattern of a wrong hardcoded hash value silently breaking proof/address computation.

### Title
Wrong hardcoded `_filled` mapping slot in `FILLED_SLOT_BIG_ENDIAN_BYTES` breaks the cross-chain "not-filled" storage proof, enabling double-settlement of intent orders - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentsBase` hardcodes the storage slot of the `_filled` mapping as `2` via `FILLED_SLOT_BIG_ENDIAN_BYTES`, which is used to build the Hyperbridge GET-request storage-proof key that verifies an order was *not* filled on the destination chain before refunding escrow on the source chain. Based on the state-variable declaration order actually visible in the contract, `_filled` is the first non-constant storage variable declared, i.e. slot `0`, not slot `2`. [1](#0-0) [2](#0-1) 

### Finding Description
`_calculateCommitmentSlotHash` computes the storage key for a Merkle/state proof exactly the way Solidity computes a mapping value's slot: `keccak256(abi.encodePacked(key, mappingSlot))`: [3](#0-2) 

`mappingSlot` here is hardcoded to `2`: [1](#0-0) 

But walking the contract's storage-variable declarations in order — `_filled` (first), `_nonce`, `_params`, `_orders`, `_instances`, `_partialFills`, `_destinationProtocolFees`, `_paused` — `_filled` is the *first* declared state variable: [4](#0-3) 

`IntentGatewayV2` inherits `IntrinsicIntents, ExtrinsicIntents, ReentrancyGuardTransient, Initializable`, with `IntentsBase` (which itself only extends `EIP712`) as the common base: [5](#0-4) 

Modern OpenZeppelin's `EIP712` stores its domain data in immutables (no storage slot), `ReentrancyGuardTransient` uses transient storage (not persistent slots), and `Initializable` (OZ 5.x) uses an ERC-7201 namespaced pseudo-random slot rather than a sequential one. None of these consume the low sequential slots ahead of `IntentsBase`'s own variables. That places `_filled` at slot `0`, not slot `2` as the constant assumes — the same class of bug as the reported `POOL_INIT_CODE_HASH` mismatch: a baked-in constant used to derive a verification key no longer matches the real on-chain data location.

The public wrapper exposes this to relayers/SDK, and it feeds directly into the cross-chain cancellation GET request: [6](#0-5) [7](#0-6) 

The SDK's off-chain order-cancellation flow calls `calculateCommitmentSlotHash` on-chain and uses its result as the storage key for the state proof fetched from the destination chain: [8](#0-7) 

If the slot constant is wrong, the storage key derived for any `commitment` points to an essentially unused/empty storage location (the mapping-value slot for a mapping that doesn't actually reside at slot `2`), so the Merkle/state proof will almost always resolve to an empty value — regardless of whether the order was genuinely filled at its real location (slot `0`). Because the same wrong constant is used both to write the proof key (in the getter) and implicitly assumed by `onGetResponse`'s trust in that proof, there is no cross-check against the real `_filled` slot; the guard silently "passes" a false statement.

### Impact Explanation
This directly hits the bounty's "false proof/state acceptance" and "replay/double-claim/double-settlement" categories. `_cancelFromSource` dispatches a GET request whose entire purpose is to prove `_filled[commitment] == address(0)` before releasing escrow back to the user via `onGetResponse` → `_withdraw`. If the proof key is derived from the wrong slot, the "order not filled" check can be satisfied even when the order *was* actually filled on the destination (real `_filled[commitment]` at slot 0 is non-zero, but the proof reads the empty slot-2-derived location instead). This lets a user obtain the destination-side filled output from a solver **and** later reclaim the source-side escrowed input tokens via a false "not filled" proof — a direct double-settlement / fund loss vector against the protocol/solver, with no privileged actor or malicious relayer required.

### Likelihood Explanation
Both `evm/src/apps/intentsv2/IntentsBase.sol` and the Tron variant `evm/tron/contracts/apps/IntentGatewayV2.sol` hardcode the identical `...002` slot constant, and the SDK unconditionally consumes `calculateCommitmentSlotHash`'s output as the trusted storage key for every cross-chain cancellation, so the path is reachable by any ordinary user calling the public `cancelOrder`/`_cancelFromSource` flow — no relayer or admin collusion needed. The main uncertainty is that I could not run the Solidity compiler's `--storage-layout` output to numerically confirm the exact slot Solidity assigns at compile time (e.g., to rule out any hidden storage consumed by the specific pinned OpenZeppelin versions used here); this should be verified with `forge inspect IntentGatewayV2 storage-layout` before treating this as conclusively confirmed.

### Recommendation
Verify the actual compiled storage slot of `_filled` with `forge inspect IntentGatewayV2 storage-layout` (or equivalent) and update `FILLED_SLOT_BIG_ENDIAN_BYTES` in both `evm/src/apps/intentsv2/IntentsBase.sol` and `evm/tron/contracts/apps/IntentGatewayV2.sol` to the correct value. Add a unit/fork test that writes to `_filled[commitment]`, fetches the real storage proof at `calculateCommitmentSlotHash(commitment)`, and asserts the decoded value equals the actual filler address — so any future storage-layout drift (e.g. from reordering base contracts or upgrading OZ) is caught automatically rather than silently breaking cross-chain cancellation proofs.

### Proof of Concept
1. Place a cross-chain order via `placeOrder` (escrows input tokens on source chain).
2. A solver fills the order on the destination chain via `fillOrder`, which sets `_filled[commitment] = solver` at the *true* storage slot (slot 0 per declaration order).
3. After the deadline, the user calls `cancelOrder`/`_cancelFromSource` on the source chain, which builds the GET-request key via `calculateCommitmentSlotHash(commitment)` — derived using the wrong slot constant `2`.
4. The destination chain's storage proof, fetched at that (wrong) key, returns an empty/zero value because nothing was ever written at the slot-2-derived location.
5. `onGetResponse` accepts the proof as evidence the order was never filled and refunds the escrowed input tokens to the user via `_withdraw`.
6. Result: the user has received the destination-chain output from the solver's fill (step 2) *and* recovered the source-chain escrow (step 5) — a double payout at the solver's/protocol's expense.

(Step 3–4 require confirming the exact compiled slot value for `_filled`, which I was not able to execute directly; this PoC should be validated with a Foundry fork test computing the real storage layout before relying on it for a bounty submission.)

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L68-73)
```text
    /**
     * @dev Big-endian encoding of storage slot 2 (the `_filled` mapping slot).
     * Used to construct storage proof keys for cross-chain cancel verification.
     */
    bytes32 constant FILLED_SLOT_BIG_ENDIAN_BYTES =
        hex"0000000000000000000000000000000000000000000000000000000000000002";
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L118-146)
```text
    /**
     * @dev Maps order commitment hashes to the address that filled or refunded the order.
     * A non-zero value indicates the order has been finalized and cannot be filled again.
     */
    mapping(bytes32 => address) public _filled;

    /**
     * @dev Monotonically increasing counter used to assign unique nonces to orders.
     * Each call to `placeOrder` consumes and increments this value.
     */
    uint256 public _nonce;

    /**
     * @dev Gateway configuration parameters including host address, dispatcher,
     * fee settings, price oracle, and solver selection toggle.
     */
    Params internal _params;

    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;

    /**
     * @dev Maps keccak256(stateMachineId) to the registered gateway address for
     * that chain. Used for authenticating cross-chain messages and routing dispatches.
     */
    mapping(bytes32 => address) public _instances;
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

**File:** evm/src/apps/IntentGatewayV2.sol (L46-75)
```text
/**
 * @title IntentGatewayV2
 * @author Polytope Labs (hello@polytope.technology)
 *
 * @dev The IntentGateway allows for the creation and fulfillment of same-chain & cross-chain orders.
 * This is the concrete entry-point contract that composes all intent logic via inheritance:
 *
 *            EIP712
 *              |
 *          IntentsBase
 *           /       \
 *  IntrinsicIntents  ExtrinsicIntents
 *           \       /
 *        IntentGatewayV2
 */
contract IntentGatewayV2 is IntrinsicIntents, ExtrinsicIntents, ReentrancyGuardTransient, Initializable {
    using SafeERC20 for IERC20;

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

**File:** evm/src/apps/IntentGatewayV2.sol (L135-143)
```text
    /**
     * @dev Computes the storage slot hash used for cross-chain cancel verification.
     * External callers (e.g., relayers) can use this to construct storage proof keys.
     * @param commitment The order commitment hash.
     * @return The ABI-encoded storage slot hash for the commitment in the `_filled` mapping.
     */
    function calculateCommitmentSlotHash(bytes32 commitment) public pure returns (bytes memory) {
        return _calculateCommitmentSlotHash(commitment);
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L173-216)
```text
    /**
     * @dev Initiates cancellation of a cross-chain order from the source chain.
     *
     * Only the order creator may cancel, and only after the order deadline has passed
     * (verified by `options.height > order.deadline`). Dispatches a Hyperbridge GET
     * request to the destination chain to verify that the `_filled` storage slot for
     * this commitment is empty (i.e., the order was never filled on the destination).
     *
     * The GET response is handled by `onGetResponse`, which refunds the escrow if
     * the slot is indeed empty.
     *
     * @param order The order to cancel.
     * @param options Cancel options including the proof height and relayer fee.
     * @param commitment The keccak256 hash of the ABI-encoded order.
     */
    function _cancelFromSource(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        if (options.height <= order.deadline) revert NotExpired();

        uint256 inputsLen = order.inputs.length;
        for (uint256 i; i < inputsLen;) {
            if (_orders[commitment][address(uint160(uint256(order.inputs[i].token)))] == 0) revert UnknownOrder();

            unchecked {
                ++i;
            }
        }

        bytes memory context =
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));

        bytes[] memory keys = new bytes[](1);
        keys[0] = bytes.concat(abi.encodePacked(_instance(order.destination)), _calculateCommitmentSlotHash(commitment));
        DispatchGet memory request = DispatchGet({
            dest: order.destination,
            keys: keys,
            timeout: 0,
            height: options.height,
            fee: options.relayerFee,
            context: context,
            payer: msg.sender
        });

```

**File:** sdk/packages/sdk/src/protocols/intents/OrderCanceller.ts (L604-616)
```typescript
				const intentGatewayV2Address = this.ctx.dest.configService.getIntentGatewayAddress(
					this.ctx.dest.config.stateMachineId,
				)
				const orderId = this.orderId(order)
				const slotHash = (await this.ctx.dest.client.readContract({
					abi: IntentGatewayV2ABI,
					address: intentGatewayV2Address,
					functionName: "calculateCommitmentSlotHash",
					args: [orderId as HexString],
				})) as HexString

				const proofHex = await this.ctx.dest.queryStateProof(latestHeight, [slotHash], intentGatewayV2Address)

```
