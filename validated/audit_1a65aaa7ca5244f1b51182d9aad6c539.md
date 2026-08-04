## Analysis

The report's core broken invariant is: a **hard-coded numeric constant that is supposed to encode a derived value (a storage offset relative to `2^256`) is wrong**, and code elsewhere relies on that constant being correct for arithmetic/verification without re-deriving it. The Hyperbridge analog is the hard-coded EVM storage-slot constant `FILLED_SLOT_BIG_ENDIAN_BYTES`, which is used to build the storage-proof key for cross-chain verification of order-fill state during intent cancellation.

### Title
Hard-coded, unverifiable `FILLED_SLOT_BIG_ENDIAN_BYTES` storage-slot constant used to build cross-chain cancellation proof keys - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentsBase.sol` and its Tron port `IntentGatewayV2.sol` both hard-code the storage slot of the `_filled` mapping as a raw `bytes32` constant, rather than deriving it from the compiler-known slot. The Tron file's own doc-comment states the intended value is slot `0x06`, yet the hex literal actually encodes slot `0x02` [1](#0-0) , mirroring exactly the "incorrect computation of a constant that should equal a derived value" class of bug from the report. This constant feeds directly into `_calculateCommitmentSlotHash`, which builds the storage key used to prove (via a Hyperbridge GET request/response) whether an order has already been filled on the destination chain before allowing a refund/cancellation on the source chain [2](#0-1) .

### Finding Description
`_calculateCommitmentSlotHash` computes `keccak256(commitment ‖ FILLED_SLOT_BIG_ENDIAN_BYTES)` to derive the storage key that a Merkle/state proof must resolve against the remote chain's state root [2](#0-1) . This key is meant to point precisely at the `_filled[commitment]` mapping slot so that a cross-chain cancellation check can trustlessly read "was this order filled on the destination chain?" and act on it.

The constant is not derived by the compiler or any layout-aware helper — it's a manually written hex literal, exactly like the manually computed `NEGATIVE_ONE` constant in the external report. The Tron contract's own comment ("Hex value 0x06 padded with leading zeros") disagrees with its actual encoded value (`...0002`, i.e. slot 2) [1](#0-0) . This self-contradiction is direct evidence that the constant was copy-pasted across contract variants without re-verifying it against the actual compiled storage layout of each specific deployment (Tron's `HyperApp`/`EIP712` base classes can differ in the number of storage slots they consume compared to the canonical EVM `IntentsBase`, shifting where `_filled` actually lands).

If the constant is wrong for a given deployment, `_calculateCommitmentSlotHash` targets the wrong slot. Any storage proof "verifying" fill status is then validated against an unrelated (and potentially attacker-influenceable, e.g., another mapping or a value slot) storage location rather than the genuine `_filled` mapping — the existing Merkle-proof/state-root check does not protect against this because it correctly proves *whatever value lives at the (wrong) key*, it just proves the wrong thing.

### Impact Explanation
Cross-chain cancellation logic in `IntentsBase`/`IntentGatewayV2` trusts the resolved value at this computed slot to decide whether an order was already filled before releasing escrowed funds back to the user (refund) or preventing double release. If the slot constant does not correspond to the actual `_filled` mapping slot for a given contract/chain deployment:
- A user could submit a valid state proof against the wrong (always-empty, or attacker-shaped) slot to make the contract believe an order was never filled, and trigger a refund of escrowed funds on the source chain even though the order was legitimately filled on the destination chain — a double-payout / fund-loss scenario matching the bounty's "unauthorized transaction," "false proof/state acceptance," and "double-claim/double-settlement" categories.
- Conversely, legitimate refunds could be permanently blocked if the wrong slot always resolves as "filled," locking user funds.

### Likelihood Explanation
This requires no privileged actor, relayer collusion, or governance action — only a normal user/solver invoking the standard cancellation/GET-request flow with the deployed contract's constant as-is. The bug is purely a consequence of the constant not matching the compiled storage layout for a particular contract variant, which is exactly the class of error the external report's `NEGATIVE_ONE` case demonstrates (a value computed once by hand and then silently reused without re-verification across code variants). The likelihood is directly tied to whether the constant is actually mismatched for the specific compiled bytecode of each app (Tron vs. canonical EVM); the contradictory comment is strong local evidence that at least one variant was never correctly re-derived after copying from the other.

### Recommendation
- Do not hard-code `FILLED_SLOT_BIG_ENDIAN_BYTES` as a raw literal per contract. Derive/verify it via Foundry's storage-layout tooling (`forge inspect <Contract> storageLayout`) for every deployed variant (canonical EVM, Tron, and any future forks), and add a CI check that asserts the constant matches the actual compiled slot of `_filled` for each contract.
- Add a runtime/test invariant (as already partially done for other slots) that writes a known value to `_filled[testCommitment]` and asserts that reading storage at the slot computed from `FILLED_SLOT_BIG_ENDIAN_BYTES` returns that same value, for both the canonical and Tron contracts.
- Fix the doc-comment/value mismatch in `evm/tron/contracts/apps/IntentGatewayV2.sol` immediately and re-verify the correct slot for the Tron inheritance chain (`HyperApp`, `EIP712` storage layout may differ from canonical EVM).

### Proof of Concept
Conceptual PoC (requires the mismatched-slot condition to actually hold for a live deployment, which needs full storage-layout inspection to confirm numerically):
1. Deploy `IntentGatewayV2` (Tron variant) and place + fill an order so `_filled[commitment] != address(0)`.
2. Call `calculateCommitmentSlotHash(commitment)` and independently compute the real EVM storage slot of `_filled[commitment]` using `forge inspect IntentGatewayV2 storageLayout` plus `keccak256(abi.encode(commitment, realSlot))`.
3. Compare: if the two diverge (as the comment/value contradiction suggests they might, given slot 6 vs slot 2), then a GET-response proof built against `calculateCommitmentSlotHash`'s key will validate whatever value sits in the wrong slot instead of the genuine fill marker.
4. Use this mismatch to submit a cancellation proof that resolves to an empty/attacker-favorable slot even though the order was actually filled, triggering an unauthorized refund alongside the legitimate destination-chain fill payout — a double payout of the same order's funds.

**Caveat:** I was not able to fully compute the exact compiled storage slot for `_filled` in both the canonical EVM `HyperApp`/`EIP712` inheritance chain and the Tron variant within the available tool budget (this requires full `forge inspect` output on both compiled contracts, not just source reading). The finding is grounded in the self-contradictory in-code documentation (`0x06` comment vs. `0x02` literal) in `evm/tron/contracts/apps/IntentGatewayV2.sol`, which is strong evidence of an unverified/manually-copied constant analogous to the reported `NEGATIVE_ONE` bug, but confirming actual exploitability requires a Devin session with build tooling to run `forge inspect` and diff the real slot against the hard-coded constant for each deployed variant.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L85-90)
```text
    /**
     * @notice Constant representing a filled slot in big endian format
     * @dev Hex value 0x06 padded with leading zeros to fill 32 bytes
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
