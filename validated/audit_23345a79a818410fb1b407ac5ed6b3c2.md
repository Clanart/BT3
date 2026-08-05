### Title
Repair-protocol request/response signatures omit a cluster/shred-version domain separator, enabling cross-cluster replay - (`core/src/repair/serve_repair.rs`)

### Summary
The Gnosis report's root cause is that `_verifySender()` authenticates *who* sent a message (the AMB address) but never binds the message to *which chain/domain* it originated from, allowing a validly-signed message from one chain to be replayed as if it came from the trusted `MIRROR_DOMAIN`. The Agave analog is in the repair protocol's signature scheme: `ServeRepair::verify_signed_packet` authenticates the `sender`/`recipient` pubkeys and a timestamp window, but the signed payload never encodes a cluster-identifying domain separator (e.g. `shred_version` or genesis hash). A signature that is valid on one Agave cluster is therefore also syntactically valid on any other cluster where the same validator identity keypair is active, because nothing in the signed bytes ties the message to a specific cluster.

### Finding Description
`ServeRepair::verify_signed_packet` computes the signed data as the packet bytes surrounding the signature field and validates it against `from_id`: [1](#0-0) 

The checks performed are: recipient-pubkey match (`header.recipient != my_id`), a timestamp freshness window (`SIGNED_REPAIR_TIME_WINDOW`), and an Ed25519 signature check binding `sender` to `signed_data`. `RepairRequestHeader` carries only `sender`, `recipient`, `timestamp`, `nonce` — none of these values are unique to a specific cluster/genesis. Unlike gossip's `ContactInfo`, which is filtered by `shred_version` at the gossip layer, the repair socket's signature-verification path (`decode_request` → `verify_signed_packet`) does not check or bind to `shred_version` or any genesis/cluster identifier before accepting the signed packet: [2](#0-1) 

Similarly, the gossip `Ping`/`Pong` signable data used for the ping-cache liveness check that gates repair responses only signs a random token/hash with a fixed, cluster-agnostic prefix (`"SOLANA_PING_PONG"`), not a cluster-specific value: [3](#0-2) [4](#0-3) 

Because the same validator identity keypair can be, and commonly is, reused across multiple Agave clusters (e.g., a validator running on both testnet and a private/forked cluster, or before/after a coordinated cluster restart that preserves identities), a signed repair request/response captured on cluster A remains a validly-signed packet on cluster B as long as the `recipient` (the target node's `my_id`) is also shared, which is the common case for the same physical validator participating in multiple clusters with the same identity key.

### Impact Explanation
This is analogous to, but weaker than, the Gnosis finding: the original bug allowed spoofing/forging of transfers because the destination chain fully trusted the sender-verified payload as domain-authentic. Here, a replayed cross-cluster repair packet could let an attacker resurrect old, cluster-A-signed repair requests/responses on cluster B to consume the ping-cache/stake-based repair budget or to inject shred data that passes `verify_signed_packet` and `ShredRepairType::verify_response` checks, even though it does not belong to the current cluster. Impact is limited by the fact that shred content is separately validated by shred/merkle-root verification in the blockstore path, so it does not directly enable false execution or fund theft; it primarily enables abuse of the unstaked/staked repair-serving budget and confuses repair bookkeeping/metrics, i.e., a low-rate, non-RPC resource-consumption vector rather than a consensus-breaking one.

### Likelihood Explanation
Exploitation requires an attacker to capture a legitimately-signed repair packet from one cluster and replay it against a node that shares both the same `recipient` identity and is reachable, and requires the target validator's identity key to be reused across clusters (which does happen operationally, e.g. testnet/devnet nodes or forked clusters, but is not guaranteed for mainnet-beta). Because the analog does not clearly cross the required "fund theft/false execution/consensus halt/non-RPC remote exhaustion" bar with a strong, unconditional path (it needs identity-key reuse across clusters, an operational assumption rather than a pure protocol flaw), confidence in this being a high-severity, unconditionally-exploitable Agave vulnerability is low.

### Recommendation
Include a cluster-identifying domain separator (the cluster `shred_version` and/or genesis hash) inside the signed data for `RepairRequestHeader`-based messages and gossip `Ping`/`Pong` payloads, and reject requests whose embedded domain does not match the local node's cluster, mirroring the `messageSourceChainId() == MIRROR_DOMAIN` fix recommended for `GnosisBase._verifySender()`.

### Proof of Concept
Not independently verified end-to-end due to tool-call limits reached during investigation. The conceptual PoC: (1) capture a signed `RepairProtocol::WindowIndex`/`Orphan`/`Pong` packet exchanged between a validator identity `V` and target `T` on cluster A; (2) replay the identical bytes to `T`'s repair socket on cluster B, where `T` also runs with `my_id` unchanged and `V`'s identity keypair is also active; (3) observe that `verify_signed_packet` accepts the packet solely based on recipient match, timestamp freshness, and Ed25519 signature — none of which differ across clusters — because no cluster/shred_version binding exists in the signed bytes, per `core/src/repair/serve_repair.rs:1446-1480`. I was unable to fully confirm within the remaining iterations whether any upstream socket-level filter (outside `serve_repair.rs`) additionally restricts repair-port traffic by `shred_version`; this should be verified against the live repository before treating this as a confirmed exploit path.

### Citations

**File:** core/src/repair/serve_repair.rs (L1053-1071)
```rust
    fn decode_request(
        remote_request: BytesPacket,
        epoch_staked_nodes: &Option<Arc<HashMap<Pubkey, u64>>>,
        whitelist: &HashSet<Pubkey>,
        my_id: &Pubkey,
        socket_addr_space: &SocketAddrSpace,
    ) -> Result<RepairRequestWithMeta> {
        let Ok(request) = deserialize_request::<RepairProtocol>(&remote_request) else {
            return Err(Error::from(RepairVerifyError::Malformed));
        };
        let from_addr = remote_request.meta().socket_addr();
        if !ContactInfo::is_valid_address(&from_addr, socket_addr_space) {
            return Err(Error::from(RepairVerifyError::Malformed));
        }
        Self::verify_signed_packet(my_id, remote_request.buffer(), &request)?;
        if request.sender() == Some(my_id) {
            error!("self repair: from_addr={from_addr} my_id={my_id} request={request:?}");
            return Err(Error::from(RepairVerifyError::SelfRepair));
        }
```

**File:** core/src/repair/serve_repair.rs (L1452-1480)
```rust
            | RepairProtocol::WindowIndexForBlockId { header, .. } => {
                if &header.recipient != my_id {
                    return Err(Error::from(RepairVerifyError::IdMismatch));
                }
                let time_diff_ms = timestamp().abs_diff(header.timestamp);
                if u128::from(time_diff_ms) > SIGNED_REPAIR_TIME_WINDOW.as_millis() {
                    return Err(Error::from(RepairVerifyError::TimeSkew));
                }
                let Some(leading_buf) = bytes.get(..4) else {
                    debug_assert!(
                        false,
                        "request should have failed deserialization: {request:?}",
                    );
                    return Err(Error::from(RepairVerifyError::Malformed));
                };
                let Some(trailing_buf) = bytes.get(4 + SIGNATURE_BYTES..) else {
                    debug_assert!(
                        false,
                        "request should have failed deserialization: {request:?}",
                    );
                    return Err(Error::from(RepairVerifyError::Malformed));
                };
                let Some(from_id) = request.sender() else {
                    return Err(Error::from(RepairVerifyError::SigVerify));
                };
                let signed_data = [leading_buf, trailing_buf].concat();
                if !header.signature.verify(from_id.as_ref(), &signed_data) {
                    return Err(Error::from(RepairVerifyError::SigVerify));
                }
```

**File:** gossip/src/ping_pong.rs (L102-121)
```rust
impl<const N: usize> Signable for Ping<N> {
    #[inline]
    fn pubkey(&self) -> Pubkey {
        self.from
    }

    #[inline]
    fn signable_data(&self) -> Cow<'_, [u8]> {
        Cow::Borrowed(&self.token)
    }

    #[inline]
    fn get_signature(&self) -> Signature {
        self.signature
    }

    fn set_signature(&mut self, signature: Signature) {
        self.signature = signature;
    }
}
```

**File:** gossip/src/ping_pong.rs (L123-140)
```rust
impl Pong {
    pub fn new<const N: usize>(ping: &Ping<N>, keypair: &Keypair) -> Self {
        let hash = hash_ping_token(&ping.token);
        Pong {
            from: keypair.pubkey(),
            hash,
            signature: keypair.sign_message(hash.as_ref()),
        }
    }

    pub fn from(&self) -> &Pubkey {
        &self.from
    }

    pub(crate) fn signature(&self) -> &Signature {
        &self.signature
    }
}
```
