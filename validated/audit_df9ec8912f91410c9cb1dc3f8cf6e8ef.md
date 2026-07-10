### Title
Hardcoded EVM Block Header Field Set Causes Permanent Proof Verification Failure After Any Future Ethereum Fork — (`File: near/omni-types/src/evm/header.rs`)

---

### Summary

The `BlockHeader` RLP decoder in `near/omni-types/src/evm/header.rs` enforces a strict "all bytes consumed" check after decoding a fixed, hardcoded set of fields. Any future Ethereum hard fork that appends a new field to the block header (as every major fork since EIP-1559 has done) will cause `rlp::decode::<BlockHeader>()` to return `RlpInconsistentLengthAndData` for every block from that fork onward. Because `verify_proof` in the EVM prover propagates this error directly, all EVM→NEAR bridge proof submissions become permanently unprocessable, freezing every in-flight and future EVM-originated bridge transfer.

---

### Finding Description

`BlockHeader` in `near/omni-types/src/evm/header.rs` decodes the Ethereum block header RLP field-by-field using a custom iterator. After consuming all known fields (the last being `requests_hash`, added in EIP-7685 / Electra), the decoder calls `is_all_bytes_consumed()`:

```rust
// near/omni-types/src/evm/header.rs  line 100-102
if !iter.is_all_bytes_consumed()? {
    return Err(DecoderError::RlpInconsistentLengthAndData);
}
```

This check rejects any RLP list that contains bytes beyond the last known field. The struct currently ends at `requests_hash: Option<H256>` (line 29), which was added for Electra. Every prior major Ethereum fork added at least one new header field:

| Fork | New field |
|---|---|
| London (EIP-1559) | `base_fee_per_gas` |
| Shanghai | `withdrawals_root` |
| Cancun | `blob_gas_used`, `excess_blob_gas`, `parent_beacon_block_root` |
| Electra (EIP-7685) | `requests_hash` |

The next scheduled fork (Osaka / Fusaka) is expected to add further fields. When it does, every post-fork block header will carry an extra RLP item that the decoder does not know about, causing `is_all_bytes_consumed()` to return `false` and the decode to fail.

The failure propagates directly into `verify_proof` in `near/omni-prover/evm-prover/src/lib.rs`:

```rust
// near/omni-prover/evm-prover/src/lib.rs  line 64
let header: BlockHeader = rlp::decode(&evm_proof.header_data).map_err(|e| e.to_string())?;
```

A decode error causes `verify_proof` to return `Err(...)`, which means the NEAR cross-contract call to the light client is never issued and the proof is rejected. There is no fallback path.

The `is_all_bytes_consumed()` check is not required for security: the block hash is computed from `rlp.as_raw()` (line 104), which covers the entire raw byte slice including any unknown trailing fields, so a header with extra fields would still produce the correct hash and be correctly validated against the light client. The check is purely a strictness guard that inadvertently makes the decoder fork-intolerant.

---

### Impact Explanation

After the next Ethereum fork that adds a header field, **every** EVM→NEAR bridge proof submission fails at the header decode step. No EVM-originated transfer can be finalized on NEAR. Funds already locked in the EVM bridge contract cannot be claimed on NEAR, and the bridge is permanently frozen for EVM chains until the NEAR contract is upgraded. This matches the allowed impact: *Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.*

---

### Likelihood Explanation

Ethereum has added at least one new block header field in every major fork since London (2021). The Osaka/Fusaka fork is actively in development and is expected to add new fields (e.g., EIP-7685 extensions or verkle-related fields). The probability of at least one new header field being added in the next fork is very high based on historical precedent. No attacker action is required; the failure is triggered automatically by any user submitting a legitimate proof for a post-fork block.

---

### Recommendation

Remove the `is_all_bytes_consumed()` strictness check, or replace it with a forward-compatible variant that silently ignores trailing unknown fields. The hash integrity check on line 104 (`keccak256(rlp.as_raw())`) already covers the full raw bytes and provides the necessary binding to the light-client-verified block hash, so the extra-bytes check adds no security value.

```rust
// Remove or relax:
// if !iter.is_all_bytes_consumed()? {
//     return Err(DecoderError::RlpInconsistentLengthAndData);
// }
```

The `Encodable` implementation should also be reviewed to ensure it does not re-encode unknown fields, but for proof verification purposes only `Decodable` matters.

---

### Proof of Concept

1. A user initiates an EVM→NEAR bridge transfer on a post-fork Ethereum block (block number `N`, where `N` is after the fork that adds a new header field).
2. The user (or relayer) calls `verify_proof` on the NEAR EVM prover contract, supplying the RLP-encoded block header for block `N` as `header_data`.
3. Inside `verify_proof` (line 64 of `near/omni-prover/evm-prover/src/lib.rs`), `rlp::decode::<BlockHeader>(&evm_proof.header_data)` is called.
4. `BlockHeader::decode` (line 73 of `near/omni-types/src/evm/header.rs`) reads all known fields including `requests_hash`, but the post-fork header contains one additional RLP item.
5. `is_all_bytes_consumed()` (line 100) returns `false` because the extra item's bytes remain unconsumed.
6. `decode` returns `Err(DecoderError::RlpInconsistentLengthAndData)`.
7. `verify_proof` returns `Err(...)` and the proof is rejected.
8. The user's funds remain locked in the EVM bridge contract with no path to claim them on NEAR. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** near/omni-types/src/evm/header.rs (L8-32)
```rust
pub struct BlockHeader {
    pub parent_hash: H256,
    pub sha3_uncles: H256,
    pub miner: Address,
    pub state_root: H256,
    pub transactions_root: H256,
    pub receipts_root: H256,
    pub logs_bloom: Bloom,
    pub difficulty: U256,
    pub number: U64,
    pub gas_limit: U256,
    pub gas_used: U256,
    pub timestamp: U64,
    pub extra_data: Vec<u8>,
    pub mix_hash: H256,
    pub nonce: H64,
    pub base_fee_per_gas: Option<U64>,
    pub withdrawals_root: Option<H256>,
    pub blob_gas_used: Option<U64>,
    pub excess_blob_gas: Option<U64>,
    pub parent_beacon_block_root: Option<H256>,
    pub requests_hash: Option<H256>,

    pub hash: Option<H256>,
}
```

**File:** near/omni-types/src/evm/header.rs (L67-69)
```rust
    fn is_all_bytes_consumed(&self) -> Result<bool, DecoderError> {
        Ok(self.rlp.as_raw().len() == self.rlp.payload_info()?.header_len + self.consumed_bytes)
    }
```

**File:** near/omni-types/src/evm/header.rs (L96-107)
```rust
            requests_hash: iter.next_option()?,
            hash: None,
        };

        if !iter.is_all_bytes_consumed()? {
            return Err(DecoderError::RlpInconsistentLengthAndData);
        }

        block_header.hash = Some(keccak256(rlp.as_raw()).into());

        Ok(block_header)
    }
```

**File:** near/omni-prover/evm-prover/src/lib.rs (L59-65)
```rust
    pub fn verify_proof(&self, #[serializer(borsh)] input: Vec<u8>) -> Result<Promise, String> {
        let args = EvmVerifyProofArgs::try_from_slice(&input)
            .map_err(|_| ProverError::ParseArgs.to_string())?;

        let evm_proof = args.proof;
        let header: BlockHeader = rlp::decode(&evm_proof.header_data).map_err(|e| e.to_string())?;
        let log_entry: LogEntry =
```
