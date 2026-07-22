### Title
`CompressedOracleV1.fallback()` First 4 Bytes Not Reserved — Function-Selector Collision Silently Drops Oracle Updates, Delivering Stale Prices to Pool Swaps - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

`CompressedOracleV1` uses a `fallback()` function as its high-throughput push path for compressed oracle slot data. Because the first 4 bytes of the calldata are not reserved with a sentinel selector, the Solidity dispatcher can silently route a legitimate oracle push to any named function in the contract (or its inherited parents), causing the update to be dropped or misapplied. Stale oracle data then propagates to every pool swap that reads from this oracle.

---

### Finding Description

`CompressedOracleV1.fallback()` expects calldata formatted as N × 32-byte slot words:

```
[data0:6][data1:6][data2:6][data3:6][ts:7][slotId:1]
```

The first 4 bytes of the first word are the top 4 bytes of the `data0` field, which encodes the compressed price `p` (uint32) followed by spread bytes `s0` and `s1`. These 4 bytes are entirely data-driven and are never reserved.

Solidity's ABI dispatcher always checks the first 4 bytes of `msg.data` against every named function selector **before** routing to `fallback()`. If the compressed price value in `data0` happens to match any of the dozens of function selectors present in `CompressedOracleV1`, `OracleBase` (compressed), `AccessControl`, or `Extsload`, the call is dispatched to that named function instead of `fallback()`.

The only guard in `fallback()` is:

```solidity
if (end == 0 || end % 32 != 0) revert BadCalldataLength();
```

This check is never reached when the dispatcher has already routed the call elsewhere. [1](#0-0) 

The `data0` field's top 4 bytes (`p` = uint32 compressed price) are the collision surface. The `_decodeCompressedOracleData` function confirms `p` occupies bits 47:16 of the 48-bit `data0` word, meaning the first 4 bytes of any push calldata are exactly the compressed price of the first slot entry: [2](#0-1) 

The same issue exists in `PythOracle.fallback()`, which also reads raw calldata from offset 0 without a reserved sentinel: [3](#0-2) 

---

### Impact Explanation

**Collision with any view function** (e.g., `getOracleData(bytes32)`, `getSlotLayout(bytes32)`, `price(bytes32,address)`, `namespaceRemapping(address)`) causes the oracle push to **silently succeed** (the transaction does not revert) while writing **no new state**. The oracle slot retains its previous (now stale) data.

Every pool swap that subsequently calls `price(feedId, pool)` on this oracle receives the stale mid/spread values. This is a direct **bad-price execution** impact: traders execute against an oracle price that no longer reflects the market, suffering losses proportional to the price drift since the last successful update.

**Collision with a state-mutating function** (e.g., `acceptStateGuardRole(bytes32)`, `setPriceGuard(bytes32,uint128,uint128)`, `grantRole(bytes32,address)`) causes unintended state changes. For example, a collision with `acceptStateGuardRole` could transfer feed custody to an address the pusher did not intend to authorize. [4](#0-3) 

---

### Likelihood Explanation

`CompressedOracleV1` and its parent contracts expose approximately 30+ named functions. The collision probability per push is approximately 30 / 2³² ≈ 7 × 10⁻⁹. The oracle is designed for high-frequency pushes (sub-second to multi-second cadence). At a 1-second push interval, a collision is statistically expected roughly once every ~4.7 years per pusher. With multiple pushers and multiple deployed instances, the aggregate probability is meaningfully higher. Critically, the collision is **silent** — the pusher's transaction succeeds, no alert fires, and the stale price persists until the next non-colliding push.

---

### Recommendation

Reserve the first 4 bytes of every push payload with a fixed sentinel that cannot match any named function selector (e.g., `0x00000000`). Validate this sentinel at the top of `fallback()` and shift all slot-word parsing to start at byte 4:

```solidity
fallback() override external {
    uint256 end;
    uint256 namespace;

    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0)) creator = msg.sender;

    assembly ("memory-safe") {
        end := calldatasize()
        namespace := shl(96, creator)
    }

    // Require sentinel prefix + at least one 32-byte word
    if (end < 36 || (end - 4) % 32 != 0) revert BadCalldataLength();

    // Validate reserved sentinel (must not collide with any function selector)
    uint32 sentinel;
    assembly ("memory-safe") {
        sentinel := shr(224, calldataload(0))
    }
    require(sentinel == 0x00000000, BadCalldataLength());

    for (uint256 ptr = 4; ptr < end; ptr += 32) {
        // ... existing slot-word processing unchanged ...
    }
}
```

Apply the same fix to `PythOracle.fallback()`.

---

### Proof of Concept

The following demonstrates a collision with `getOracleData(bytes32)` (selector `0x...`) causing a silent push failure:

```solidity
// selector of getOracleData(bytes32) = keccak256("getOracleData(bytes32)")[0:4]
// Craft a 32-byte slot word whose top 4 bytes equal that selector.
// The push call succeeds (no revert) but oracle state is unchanged.
// The next pool swap reads the prior stale price.

bytes32 craftedWord = bytes32(
    uint256(bytes4(keccak256("getOracleData(bytes32)"))) << 224
    | uint256(someValidTimestampAndSlotId)
);
(bool ok, ) = address(oracle).call(abi.encodePacked(craftedWord));
assertEq(ok, true); // succeeds — routed to getOracleData, not fallback
// oracle slot is unchanged; stale price persists
```

The same pattern applies to any of the 30+ named functions in the contract hierarchy. A collision with `acceptStateGuardRole(bytes32)` would additionally transfer feed custody if a pending guard was set. [5](#0-4) [4](#0-3)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L145-149)
```text
    function _decodeCompressedOracleData(uint48 raw) internal pure returns (CompressedOracleData memory data) {
        data.p = uint32(raw >> 16);
        data.s0 = uint8((raw >> 8) & 0xFF);
        data.s1 = uint8(raw & 0xFF);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-344)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }

        // 4 * 6 + 7 + 1 = 32 bytes per slot
        if (end == 0 || end % 32 != 0) revert BadCalldataLength();

        for (uint256 ptr = 0; ptr < end; ptr += 32) {
            uint256 word;
            assembly ("memory-safe") {
                word := calldataload(ptr)
            }
            // casting to 'uint8' is safe we want LSB
            // forge-lint: disable-next-line(unsafe-typecast)
            uint8 slotId = uint8(word);
            TimeMs timestampMs = toTimeMs(word >> 8 & X56);
            timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
            bytes32 key = bytes32(namespace | uint256(slotId));
            uint256 old = uint256(_loadStorage(key));
            TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
        }
```

**File:** smart-contracts-poc/contracts/oracles/providers/PythOracle.sol (L39-72)
```text
    fallback() payable external override {
        uint256 end;

        assembly ("memory-safe") {
            end := calldatasize()
        }

        uint256 feedsLength;
        assembly ("memory-safe") {
            feedsLength := shr(240, calldataload(0)) // first 2 bytes
        }

        uint32[] memory updateFeedIds = new uint32[](feedsLength);
        assembly ("memory-safe") {
            let dst := add(updateFeedIds, 32)  // skip length slot
            let src := 2                       // offset after feedsLength(2)

            for { let i := 0 } lt(i, feedsLength) { i := add(i, 1) } {
                // load 32 bytes, shift right to get uint32 from high bits
                mstore(dst, shr(224, calldataload(src)))
                dst := add(dst, 32)
                src := add(src, 4)
            }
        }

        uint256 priceUpdateOffset = 2 + feedsLength * 4;
        bytes calldata priceUpdate;
        assembly ("memory-safe") {
            priceUpdate.offset := priceUpdateOffset
            priceUpdate.length := sub(end, priceUpdateOffset)
        }

        _verifyAndStore(oracleData, updateFeedIds, priceUpdate);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L72-78)
```text
    function acceptStateGuardRole(bytes32 feedId) external {
        require(pendingStateGuard[feedId] == msg.sender, InvalidGuard(msg.sender));

        delete pendingStateGuard[feedId];
        stateGuard[feedId] = msg.sender;

        emit StateGuardUpdated(feedId, msg.sender);
```
