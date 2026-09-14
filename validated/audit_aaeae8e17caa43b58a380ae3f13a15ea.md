Confirmed: `respond_signatures` at `chia/farmer/farmer_api.py:625-637` has **no `peer_required` parameter and no peer-identity check at all** — it processes any `RespondSignatures` message from *any* connection whose negotiated `NodeType` is in `ProtocolMessageTypeToNodeType[respond_signatures] = {NodeType.HARVESTER}` [1](#0-0) , and `_process_respond_signatures()` correlates purely by `response.sp_hash` / `response.plot_identifier` against `self.farmer.sps` / `self.farmer.proofs_of_space` — never checking which peer sent it or matching it to the harvester that originally supplied the proof [2](#0-1) .

### Title
Peer-role self-declaration in the P2P handshake lets any private-CA-cert holder impersonate a Harvester and redirect farmer block rewards - (File: chia/server/ws_connection.py)

### Summary
Chia's TLS layer authenticates only that a peer's certificate was signed by the trusted CA (public or the user's private CA); it never binds the specific service *role* to the certificate. The peer's `NodeType` is instead a self-declared field inside the `Handshake` payload, taken at face value. Combined with `respond_signatures` having no peer-identity binding in the Farmer, any local process holding *any* private-CA-signed certificate (harvester/wallet/data_layer/daemon, all sharing one private CA per `chia/ssl/create_ssl.py`) can connect to a Farmer, declare `NodeType.HARVESTER` in the handshake, and inject a `RespondSignatures` with `farmer_reward_address_override` set, causing the Farmer to redirect the block-reward puzzle hash in the `DeclareProofOfSpace` message it broadcasts to full nodes.

### Finding Description
`ChiaServer.create()` only distinguishes clients/servers by whether they use the *private* CA vs the *public* Chia CA — Farmer, Wallet, Data Layer, and Harvester servers all accept **the same private-CA-signed client certificate class** [3](#0-2) . TLS only proves "signed by this CA", not "this is specifically a Harvester cert" — this mirrors the Nomad CVE-2021-37218 pattern where any peer holding a valid same-CA certificate could access server-only functionality regardless of its actual role.

The actual role assertion happens post-TLS, in `WSChiaConnection.perform_handshake()`, where `remote_node_type = NodeType(inbound_handshake.node_type)` is taken directly from the peer-supplied `Handshake.node_type` field with no cross-check against the certificate used [4](#0-3) . `connection_type` is then trusted everywhere downstream — sender authorization (`ProtocolMessageTypeToNodeType`), `Farmer.on_connect()`'s `peer.connection_type is NodeType.HARVESTER` branch [5](#0-4) , and Farmer's protocol handlers.

Once connected as a fake "Harvester", the attacker can send an unsolicited `RespondSignatures` (authorized for `NodeType.HARVESTER` senders) referencing any `sp_hash`/`plot_identifier` currently cached by the Farmer (these are broadcast to every real harvester via `new_signage_point_harvester`, so the values are observable/predictable by any connected peer) with `farmer_reward_address_override` set. `_process_respond_signatures()` performs no check that the response came from the peer_node_id recorded for that proof in `quality_str_to_identifiers`/`proofs_of_space` [2](#0-1) , and the override is honored: `farmer_reward_address = response.farmer_reward_address_override` [6](#0-5) , which is then placed directly into the `DeclareProofOfSpace` message broadcast to all connected full nodes [7](#0-6) .

The BLS signature checks in this path (`AugSchemeMPL.verify(agg_pk, ...)`) validate the *plot*/*farmer* signature shares for the real proof already cached, but do not bind or validate `farmer_reward_address_override` itself — it is accepted as plain unauthenticated data from whichever connection sent the `RespondSignatures`.

### Impact Explanation
If the crafted `DeclareProofOfSpace` is accepted by a full node and the corresponding proof wins a block, the block's farmer reward output is redirected to the attacker-chosen puzzle hash instead of the legitimate farmer's configured `farmer_target` — this is direct **reward redirection**, matching the explicitly listed acceptable impact category. This requires no possession of the real harvester's plot keys; the attacker only needs a TLS connection authenticated with *any* certificate signed by the operator's private CA and knowledge of an in-flight `sp_hash`/`plot_identifier`, both broadcast to all connected harvester-typed peers.

### Likelihood Explanation
Exploitation requires the attacker to already hold a private-CA-signed certificate for *some* service on the same node (e.g., as a legitimate Data Layer client, or any other private-cert holder) — this is a real but non-trivial local-trust-boundary weakness rather than a fully unauthenticated remote attack. It's also gated on winning a real, live signage-point/proof race (timing window), and CHIP-22 documents `farmer_reward_address_override` as an intentionally-supported convention for third-party harvesters ("a reward-routing convention, not a consensus permission check"), suggesting the underlying design gap — no verification that the override truly originates from the harvester that supplied the winning proof — has not been hardened even though it was a known design tradeoff.

### Recommendation
- Bind the TLS certificate identity to the declared `NodeType` at handshake time (e.g., issue distinct per-role CAs/certs, or embed the intended role in the certificate and enforce it against `Handshake.node_type`), so a private-cert holder for one role cannot self-declare a different role.
- In `Farmer._process_respond_signatures()` / `respond_signatures()`, require `peer_required=True` and verify the sending peer's `peer_node_id` matches the harvester (`node_id`) originally recorded for that `sp_hash`/`plot_identifier` in `quality_str_to_identifiers` before honoring `farmer_reward_address_override` or forwarding the signatures.
- Consider requiring a source-signature/authenticity proof over `farmer_reward_address_override` tied to the plot's key material rather than trusting the field verbatim from the connection.

### Proof of Concept
1. Obtain any certificate signed by the target node's private CA (e.g., the operator's own Data Layer or Wallet private cert under `config/ssl/`).
2. Open a TLS client connection to the Farmer's server port using that certificate; in the P2P `Handshake` payload, set `node_type = NodeType.HARVESTER.value`. The connection is admitted because TLS only validates the CA signature, and `WSChiaConnection.perform_handshake()` sets `connection_type` from the self-declared field.
3. Observe legitimate `new_signage_point_harvester` broadcasts to learn a live `sp_hash`; wait for/observe a genuine harvester's real proof being cached (`Farmer.proofs_of_space[sp_hash]`, `quality_str_to_identifiers`).
4. Send an unsolicited `RespondSignatures` message referencing that `sp_hash`/`plot_identifier` with a valid `local_pk`/`farmer_pk`/`message_signatures` (reusable/observable from the legitimate harvester's real response) plus `farmer_reward_address_override` set to an attacker-controlled puzzle hash.
5. The Farmer's `respond_signatures()` handler accepts the message (sender authorized as `NodeType.HARVESTER`), and `_process_respond_signatures()` builds and broadcasts `DeclareProofOfSpace` with the attacker's reward address to all connected full nodes.

### Citations

**File:** chia/protocols/protocol_message_type_to_node_type.py (L17-17)
```python
    ProtocolMessageTypes.respond_signatures: {NodeType.HARVESTER},
```

**File:** chia/farmer/farmer_api.py (L853-881)
```python
    def _process_respond_signatures(
        self, response: harvester_protocol.RespondSignatures
    ) -> DeclareProofOfSpace | SignedValues | None:
        """
        Processing the responded signatures happens when receiving an unsolicited request for an SP or when receiving
        the signature response for a block from a harvester.
        """
        if response.sp_hash not in self.farmer.sps:
            self.farmer.log.warning(f"Do not have challenge hash {response.challenge_hash}")
            return None
        is_sp_signatures: bool = False
        sps = self.farmer.sps[response.sp_hash]
        peak_height = sps[0].peak_height
        last_tx_height = sps[0].last_tx_height
        signage_point_index = sps[0].signage_point_index
        found_sp_hash_debug = False
        for sp_candidate in sps:
            if response.sp_hash == response.message_signatures[0][0]:
                found_sp_hash_debug = True
                if sp_candidate.reward_chain_sp == response.message_signatures[1][0]:
                    is_sp_signatures = True
        if found_sp_hash_debug:
            assert is_sp_signatures

        pospace = None
        for plot_identifier, candidate_pospace in self.farmer.proofs_of_space[response.sp_hash]:
            if plot_identifier == response.plot_identifier:
                pospace = candidate_pospace
        assert pospace is not None
```

**File:** chia/farmer/farmer_api.py (L950-953)
```python
                    farmer_reward_address = self.farmer.farmer_target
                    if response.farmer_reward_address_override is not None:
                        farmer_reward_address = response.farmer_reward_address_override
                        include_source_signature_data = True
```

**File:** chia/farmer/farmer_api.py (L955-967)
```python
                    return farmer_protocol.DeclareProofOfSpace(
                        response.challenge_hash,
                        challenge_chain_sp,
                        signage_point_index,
                        reward_chain_sp,
                        pospace,
                        agg_sig_cc_sp,
                        agg_sig_rc_sp,
                        farmer_reward_address,
                        pool_target,
                        pool_target_signature,
                        include_signature_source_data=include_source_signature_data,
                    )
```

**File:** chia/server/server.py (L170-216)
```python
        authenticated_client_types = {NodeType.HARVESTER}
        authenticated_server_types = {
            NodeType.HARVESTER,
            NodeType.FARMER,
            NodeType.WALLET,
            NodeType.DATA_LAYER,
        }

        if local_type in authenticated_client_types:
            # Authenticated clients
            private_cert_path, private_key_path = private_ssl_paths(root_path, config)
            ssl_client_context = ssl_context_for_client(
                ca_cert=ca_private_crt_path,
                ca_key=ca_private_key_path,
                cert_path=private_cert_path,
                key_path=private_key_path,
            )
        else:
            # Public clients
            public_cert_path, public_key_path = public_ssl_paths(root_path, config)
            ssl_client_context = ssl_context_for_client(
                ca_cert=chia_ca_crt_path,
                ca_key=chia_ca_key_path,
                cert_path=public_cert_path,
                key_path=public_key_path,
            )

        if local_type in authenticated_server_types:
            # Authenticated servers
            private_cert_path, private_key_path = private_ssl_paths(root_path, config)
            ssl_context = ssl_context_for_server(
                ca_cert=ca_private_crt_path,
                ca_key=ca_private_key_path,
                cert_path=private_cert_path,
                key_path=private_key_path,
                log=log,
            )
        else:
            # Public servers
            public_cert_path, public_key_path = public_ssl_paths(root_path, config)
            ssl_context = ssl_context_for_server(
                ca_cert=chia_ca_crt_path,
                ca_key=chia_ca_key_path,
                cert_path=public_cert_path,
                key_path=public_key_path,
                log=log,
            )
```

**File:** chia/server/ws_connection.py (L253-306)
```python
    async def perform_handshake(
        self,
        network_id: str,
        server_port: int,
        local_type: NodeType,
    ) -> None:
        if self.is_outbound:
            outbound_handshake = make_msg(
                ProtocolMessageTypes.handshake,
                Handshake(
                    network_id,
                    protocol_version[local_type],
                    __version__,
                    uint16(server_port),
                    uint8(local_type.value),
                    self.local_capabilities_for_handshake,
                ),
            )
            await self._send_message(outbound_handshake)

        try:
            message = await self._read_one_message()
        except Exception:
            raise ProtocolError(Err.INVALID_HANDSHAKE)

        if message is None:
            raise ProtocolError(Err.INVALID_HANDSHAKE)

        # Handle case of invalid ProtocolMessageType
        try:
            inbound_handshake = Handshake.from_bytes(message.data)
            message_type = ProtocolMessageTypes(message.type)
        except Exception:
            raise ProtocolError(Err.INVALID_HANDSHAKE)

        if message_type != ProtocolMessageTypes.handshake:
            raise ProtocolError(Err.INVALID_HANDSHAKE)

        if inbound_handshake.network_id != network_id:
            raise ProtocolError(Err.INCOMPATIBLE_NETWORK_ID)

        if (
            self.is_outbound
            and local_type in {NodeType.FARMER, NodeType.HARVESTER}
            and inbound_handshake.protocol_version != protocol_version[local_type]
        ):
            self.log.warning(
                f"protocol version mismatch: "
                f"local_type={local_type} "
                f"incoming={inbound_handshake.protocol_version} "
                f"our={protocol_version[local_type]}"
            )

        remote_node_type = NodeType(inbound_handshake.node_type)
```

**File:** chia/farmer/farmer.py (L377-379)
```python
        if peer.connection_type is NodeType.HARVESTER:
            self.plot_sync_receivers[peer.peer_node_id] = Receiver(peer, self.plot_sync_callback, self.constants)
            self.harvester_handshake_task = create_referenced_task(handshake_task())
```
