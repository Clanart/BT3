Based on the codebase evidence, there's a directly analogous bug-class match in Chia's DataLayer RPC surface: the `add_missing_files` endpoint accepts a fully attacker/caller-controlled destination directory with no restriction to a safe base path, mirroring the `check_sftp` CVE-2024-28826 pattern (unrestricted upload/download paths controlled by a configured/RPC parameter).

### Title
Unrestricted local file-write path via `foldername` parameter in DataLayer `add_missing_files` RPC - (File: chia/data_layer/data_layer_rpc_api.py)

### Summary
The DataLayer RPC endpoint `/add_missing_files` accepts an optional `foldername` request field and converts it directly into a `Path` with no validation, containment check, or restriction to the service's `server_files_location`. That path is then used as the write target for `.dat` tree/delta files.

### Finding Description
In `DataLayerRpcApi.add_missing_files()`, the `foldername` field from the request is taken verbatim and turned into a `Path`: [1](#0-0) 

This is passed straight into `DataLayer.add_missing_files()`, which uses it as `server_files_location` for every generation's file write, with no check that it stays under the daemon's configured `server_files_location`: [2](#0-1) 

The actual write happens in `write_files_for_root()`, which joins the caller-supplied folder with filenames built from internal store id/hash/generation values and opens them for writing (`"wb"` when `overwrite=True`): [3](#0-2) 

Unlike the static file server (`chia/data_layer/data_layer_server.py`), which validates untrusted filenames via `is_filename_valid()` before joining them to a fixed `server_files_location`, this RPC-side write path validates only the filename *components*, not the *destination directory* — the directory itself is fully caller-controlled and unrestricted. There is no allowlist, no `resolve()`/containment check against the configured `server_files_location`, and `overwrite=True` permits clobbering existing files at that arbitrary location.

### Impact Explanation
Any RPC caller with access to the DataLayer daemon's RPC (the same actor class as the "local RPC caller" the rules explicitly list as in-scope) can direct the daemon process to write/overwrite `.dat` files anywhere the process has filesystem permissions — including outside the intended `data_layer/db/server_files_location_*` tree. Combined with `overwrite=True`, this allows overwriting existing files at attacker-chosen paths (subject to write permissions of the chia daemon process), which is the direct analog of the Checkmk `check_sftp` "unrestricted upload/download local paths" bug class (CVE-2024-28826).

### Likelihood Explanation
Reaching this requires only a call to the DataLayer RPC's `add_missing_files` endpoint with a `foldername` value, which is a normal, documented parameter (also exposed via the `chia data add_missing_files -d/--directory` CLI command and `data_layer_rpc_client.py`). No chain state, singleton ownership, or special permission beyond RPC access is required. [4](#0-3) [5](#0-4) 

### Recommendation
Resolve `foldername` and reject it (or fall back to the default) unless it resolves to a path contained within the configured `server_files_location` (or another explicitly allow-listed base directory). Apply the same containment check used implicitly by the static server's directory model, and avoid trusting a raw RPC string as a filesystem write root.

### Proof of Concept
1. Start a chia daemon with DataLayer RPC enabled and at least one owned/tracked store with committed generations.
2. Call `/add_missing_files` with `{"ids": ["<store_id>"], "overwrite": true, "foldername": "/etc/chia_poc"}` (or any path outside `server_files_location`, e.g. a shared web root or a sensitive config directory the daemon user can write to).
3. Observe `.dat` files being written under the attacker-specified directory instead of the sandboxed `server_files_location`, confirming unrestricted destination-path control from an RPC caller.

### Citations

**File:** chia/data_layer/data_layer_rpc_api.py (L411-416)
```python
        overwrite = request.get("overwrite", False)
        foldername: Path | None = None
        if "foldername" in request:
            foldername = Path(request["foldername"])
        for store_id in ids_bytes:
            await self.service.add_missing_files(store_id, overwrite, foldername)
```

**File:** chia/data_layer/data_layer.py (L843-869)
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

**File:** chia/data_layer/download_data.py (L69-107)
```python
async def write_files_for_root(
    data_store: DataStore,
    store_id: bytes32,
    root: Root,
    foldername: Path,
    full_tree_first_publish_generation: int,
    overwrite: bool = False,
    group_by_store: bool = False,
) -> WriteFilesResult:
    if root.node_hash is not None:
        node_hash = root.node_hash
    else:
        node_hash = bytes32.zeros  # todo change

    filename_full_tree = get_full_tree_filename_path(foldername, store_id, node_hash, root.generation, group_by_store)
    filename_diff_tree = get_delta_filename_path(foldername, store_id, node_hash, root.generation, group_by_store)
    filename_full_tree.parent.mkdir(parents=True, exist_ok=True)

    written = False
    mode: Literal["wb", "xb"] = "wb" if overwrite else "xb"

    written_full_file = False
    if root.generation >= full_tree_first_publish_generation:
        try:
            with open(filename_full_tree, mode) as writer:
                await data_store.write_tree_to_file(root, node_hash, store_id, False, writer)
            written = True
            written_full_file = True
        except FileExistsError:
            pass

    try:
        with open(filename_diff_tree, mode) as writer:
            await data_store.write_tree_to_file(root, node_hash, store_id, True, writer)
        written = True
    except FileExistsError:
        pass

    return WriteFilesResult(written, filename_full_tree if written_full_file else None, filename_diff_tree)
```

**File:** chia/data_layer/data_layer_rpc_client.py (L119-130)
```python
    async def add_missing_files(
        self, store_ids: list[bytes32] | None, overwrite: bool | None, foldername: Path | None
    ) -> dict[str, Any]:
        request: dict[str, Any] = {}
        if store_ids is not None:
            request["ids"] = [store_id.hex() for store_id in store_ids]
        if overwrite is not None:
            request["overwrite"] = overwrite
        if foldername is not None:
            request["foldername"] = str(foldername)
        response = await self.fetch("add_missing_files", request)
        return response
```

**File:** chia/cmds/data.py (L435-471)
```python
@data_cmd.command("add_missing_files", help="Manually reconstruct server files from the data layer database")
@click.option(
    "-i",
    "--ids",
    help="List of stores to reconstruct. If not specified, all stores will be reconstructed",
    type=str,
    multiple=True,
    required=False,
)
@click.option(
    "-o/-n",
    "--overwrite/--no-overwrite",
    help="Specify if already existing files need to be overwritten by this command",
)
@click.option(
    "-d", "--directory", type=str, help="If specified, use a non-default directory to write the files", required=False
)
@create_rpc_port_option()
@options.create_fingerprint()
def add_missing_files(
    ids: Sequence[bytes32],
    overwrite: bool,
    directory: str | None,
    data_rpc_port: int,
    fingerprint: int | None,
) -> None:
    from chia.cmds.data_funcs import add_missing_files_cmd

    run(
        add_missing_files_cmd(
            rpc_port=data_rpc_port,
            ids=list(ids) if ids else None,
            overwrite=overwrite,
            foldername=None if directory is None else Path(directory),
            fingerprint=fingerprint,
        )
    )
```
