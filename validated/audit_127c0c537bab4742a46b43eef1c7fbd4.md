### Title
Unvalidated `foldername` in DataLayer RPC `add_missing_files` allows arbitrary filesystem write/overwrite by a local RPC caller - (File: chia/data_layer/data_layer_rpc_api.py)

### Summary
The Asterisk CVE involves a restricted, semi-trusted client (`write=originate` AMI user) being able to abuse a file-writing primitive to write/overwrite arbitrary files outside the intended directory, leading to config tampering and potential RCE. The analogous pattern exists in the Chia DataLayer RPC service: the `/add_missing_files` endpoint accepts a caller-supplied `foldername` string and uses it, unvalidated, as the destination directory for writing DataLayer `.dat` tree files to disk.

### Finding Description
`DataLayerRpcApi.add_missing_files()` reads an optional `foldername` directly from the RPC request and converts it to a `Path` with no normalization, containment check, or restriction to the configured `server_files_location`: [1](#0-0) 

That path is passed straight into `DataLayer.add_missing_files()`, which — if provided — overrides `self.server_files_location` as the destination for writing full/delta tree files for every generation of the store: [2](#0-1) 

The actual write path construction (`write_files_for_root`) and any pre-existing file at the computed destination is only guarded by an `overwrite` boolean also supplied by the caller (`request.get("overwrite", False)`), not by any allow-list of directories. Because `foldername` is a free-form path from the request, a caller of the DataLayer RPC (any local RPC client authorized to hit the DataLayer JSON-RPC, which is a lower-trust surface than the wallet/full-node RPC and is designed for semi-automated/plugin use) can direct file-writes to any writable path reachable by the `chia_data_layer` process, including paths outside the intended `server_files_location` sandbox — e.g. into the user's `~/.chia/<net>/data_layer/` config area or other locations writable by that process's user, when `overwrite=True` is also supplied.

This mirrors the Asterisk bug class: a permitted-but-limited RPC action (`write=originate` in Asterisk, DataLayer file-serving RPC here) is repurposed via a file-path-controlling parameter to write to locations the caller should not control, without any path containment enforcement.

### Impact Explanation
An attacker with access to the DataLayer RPC socket (a lower-privilege, automation-oriented surface compared to keychain-holding wallet RPC) can force the service to write/overwrite arbitrary files at attacker-chosen paths with content derived from local tree state. Depending on file system permissions this can corrupt other DataLayer-managed files, collide with unrelated files sharing a name in a targeted directory, or be chained with other primitives to influence files consumed by other components of the node (e.g. other `.dat` files served to peers from a different, now-tampered directory), enabling data corruption / potential service disruption on the DataLayer file-serving path. This does not directly forge on-chain coin state, but it is a concrete unauthorized-write primitive reachable purely through a permitted Data Layer RPC call.

### Likelihood Explanation
Requires that the caller already has access to the `chia_data_layer` RPC (a local RPC caller in this codebase's trust model, analogous to the semi-trusted AMI user in the CVE). Given that access, the exploit is trivial: a single `add_missing_files` call with a crafted `foldername` and `overwrite=true`. No CLVM crafting, no chain interaction, and no additional privilege is needed beyond RPC access to this one endpoint.

### Recommendation
Validate and canonicalize `foldername` against an explicit allow-list (e.g., must resolve under the configured `server_files_location` or a small set of approved directories) before use in `DataLayer.add_missing_files()`. Reject paths containing traversal segments or absolute paths outside the sandbox, and consider removing the caller-controlled `foldername` override entirely in favor of a server-side configured location only.

### Proof of Concept
1. Start `chia_data_layer` with RPC access (as a local RPC caller with permission to call DataLayer RPC endpoints).
2. Own or subscribe to a store with at least one generation so `add_missing_files` has data to write.
3. Call the DataLayer RPC endpoint:
```
POST /add_missing_files
{
  "ids": ["<store_id_hex>"],
  "overwrite": true,
  "foldername": "/path/attacker/controls/outside/server_files_location"
}
```
4. Observe that `chia/data_layer/data_layer_rpc_api.py:add_missing_files` (lines 401-417) passes this path unchanged to `DataLayer.add_missing_files` (`chia/data_layer/data_layer.py:843-870`), which writes `.dat` files into that directory regardless of the configured `server_files_location`, confirming the unauthorized write outside the intended sandbox.

### Citations

**File:** chia/data_layer/data_layer_rpc_api.py (L401-417)
```python
    async def add_missing_files(self, request: dict[str, Any]) -> EndpointResult:
        """
        complete the data server files.
        """
        if "ids" in request:
            store_ids = request["ids"]
            ids_bytes = [bytes32.from_hexstr(id) for id in store_ids]
        else:
            subscriptions: list[Subscription] = await self.service.get_subscriptions()
            ids_bytes = [subscription.store_id for subscription in subscriptions]
        overwrite = request.get("overwrite", False)
        foldername: Path | None = None
        if "foldername" in request:
            foldername = Path(request["foldername"])
        for store_id in ids_bytes:
            await self.service.add_missing_files(store_id, overwrite, foldername)
        return {}
```

**File:** chia/data_layer/data_layer.py (L843-870)
```python
    async def add_missing_files(self, store_id: bytes32, overwrite: bool, foldername: Path | None) -> None:
        root = await self.data_store.get_tree_root(store_id=store_id)
        latest_generation = root.generation
        full_tree_first_publish_generation = max(0, latest_generation - self.maximum_full_file_count + 1)
        singleton_record = (
            await self.wallet_rpc.dl_latest_singleton(DLLatestSingleton(launcher_id=store_id, only_confirmed=True))
        ).singleton
        if singleton_record is None:
            self.log.error(f"No singleton record found for: {store_id}")
            return
        max_generation = min(singleton_record.generation, root.generation)
        server_files_location = foldername if foldername is not None else self.server_files_location
        files = []
        for generation in range(1, max_generation + 1):
            root = await self.data_store.get_tree_root(store_id=store_id, generation=generation)
            res = await write_files_for_root(
                self.data_store,
                store_id,
                root,
                server_files_location,
                full_tree_first_publish_generation,
                overwrite,
                self.group_files_by_store,
            )
            files.append(res.diff_tree.name)
            if res.full_tree is not None:
                files.append(res.full_tree.name)

```
