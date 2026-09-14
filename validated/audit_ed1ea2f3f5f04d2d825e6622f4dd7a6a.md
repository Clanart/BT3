### Title
Unbounded recursive Merkle-tree traversal in DataLayer file generation can stack-overflow the DataLayer service - (File: chia/data_layer/data_store.py)

### Summary
`DataStore.get_nodes_for_file()` recursively walks a store's Merkle tree with no depth bound, and is invoked by `write_tree_to_file()` every time DataLayer serializes a root to a `.dat` file (full or delta) for server/mirror distribution. A store's tree shape is not always auto-balanced: `DataStore.insert()` / `DataStore.insert_batch()` allow a caller to supply an explicit `reference_node_hash`/`side` per key, bypassing the random-reference auto-balancing path (`get_reference_kid_side`) that keeps depth roughly `O(log n)`. By chaining every new key onto the previously inserted key with a fixed `side`, a caller can build a maximally skewed (linked-list-shaped) binary tree whose depth equals the number of keys inserted. When that store is later serialized (an automatic, non-optional step in `write_files_for_root()`/`periodically_manage_data()`), the unbounded Python recursion in `get_nodes_for_file()` can exhaust the call stack, matching the CVE-2024-1151 bug class of a recursive push with no depth validation leading to a stack overflow / crash.

### Finding Description
`get_nodes_for_file()` recurses once per internal node with no depth check or Python `sys.setrecursionlimit` protection: [1](#0-0) 

It is called from `write_tree_to_file()`, which is the function used to generate both full-tree and delta `.dat` files for every committed root: [2](#0-1) 

`write_files_for_root()` calls `write_tree_to_file()` unconditionally for every root generation as part of the DataLayer service's own file-generation lifecycle: [3](#0-2) 

The tree depth is normally kept shallow because `insert()`/`insert_batch()` default to randomized reference selection (`get_reference_kid_side`), which the project's own test asserts keeps depth to roughly `log2(n)` for thousands of entries: [4](#0-3) 

However, both `insert()` and each `"insert"` entry in `insert_batch()`'s changelist accept an explicit `reference_node_hash`/`side`, which is used verbatim instead of the balancing seed when supplied: [5](#0-4) [6](#0-5) 

Nothing in `insert()`/`insert_batch()` bounds the resulting tree depth when explicit references are used, so a party who controls a changelist (the store owner via local RPC, or an offer counterparty whose offered-store data is staged locally by `process_offered_stores()`/DataLayer offer handling) can construct an arbitrarily deep, degenerate tree. The project already acknowledges recursion-depth risk for externally-loaded trees — `chia_rs.datalayer.MerkleBlob` raises `RecursionDepthExceededError` when *reading* a maliciously deep tree from a delta/full file — but that guard exists on the ingestion path (`insert_into_data_store_from_file`), not on the Python-side `get_nodes_for_file()` recursion used when *writing* files for a locally-built, degenerate tree: [7](#0-6) 

### Impact Explanation
A sufficiently deep chained-reference tree causes `get_nodes_for_file()`'s recursion to exceed the interpreter's safe stack usage. Because this call happens inside `write_files_for_root()`, which runs automatically as part of `DataLayer.periodically_manage_data()`/`update_subscription()` for every committed root of every owned or subscribed store, this can crash or hang the DataLayer service process (denial of service to that component), analogous to the OVS stack-overflow bug class in the CVE: an unbounded recursive operation with no depth validation that a local actor can trigger through normal operations exposed to them (batch update / offer processing), rather than needing a malicious peer or privileged access.

### Likelihood Explanation
Building the necessary degenerate tree only requires normal, documented `insert`/`insert_batch` parameters (`reference_node_hash`, `side`) that any DataLayer client already uses for explicit placement; no protocol violation or malformed data is needed. The file-generation step that triggers the recursive walk is not optional — it runs automatically on every commit/subscription cycle, so the crash condition doesn't require a separate deliberate trigger by an attacker beyond the store population.

### Recommendation
- Convert `get_nodes_for_file()` (and its sibling `write_tree_to_file_old_format()` test helper's production equivalent) to an iterative, explicit-stack traversal, mirroring the non-recursive design already used in `chia/types/blockchain_format/tree_hash.py`'s `sha256_treehash()`.
- Alternatively/additionally, enforce a maximum tree depth in `DataStore.insert()`/`insert_batch()` when an explicit `reference_node_hash`/`side` is supplied, rejecting inserts that would create pathological depth, consistent with the depth guard `chia_rs.datalayer.MerkleBlob` already applies on the file-ingestion path.

### Proof of Concept
1. Create a DataLayer store and insert `N` (e.g., 50,000) keys, each insert specifying `reference_node_hash` equal to the previously-inserted leaf's hash and a fixed `side` (e.g., `Side.LEFT`) — this is exactly the API shape exercised in `test_insert_batch_reference_and_side` (chia/_tests/core/data_layer/test_data_store.py:504-545), but repeated N times instead of once, which produces a maximally unbalanced chain of depth `N` instead of a balanced tree.
2. Commit the batch (`insert_batch(..., status=Status.COMMITTED)`), causing `_insert_root`/`insert_root_from_merkle_blob` to store the new root.
3. Trigger file generation for that root, either directly via `write_files_for_root(data_store, store_id, root, ...)` (as done in `chia/_tests/core/data_layer/test_data_store.py:1673-1687`) or indirectly by letting `DataLayer.periodically_manage_data()` run its normal cycle.
4. Observe that `write_tree_to_file()` → `get_nodes_for_file()` recurses to depth `N`, exhausting the Python call stack and raising `RecursionError` (or, depending on stack size/thread, crashing the process) instead of completing file generation.

### Citations

**File:** chia/data_layer/data_store.py (L1330-1347)
```python
                kid, vid = await self.add_key_value(key, value, store_id, writer=writer)
                hash = leaf_hash(key, value)
                reference_kid = None
                if reference_node_hash is not None:
                    reference_kid, _ = merkle_blob.get_node_by_hash(reference_node_hash)

                was_empty = root.node_hash is None
                if not was_empty and reference_kid is None:
                    if side is not None:
                        raise Exception("Side specified without reference node hash")

                    seed = leaf_hash(key=key, value=value)
                    reference_kid, side = self.get_reference_kid_side(merkle_blob, seed)

                merkle_blob.insert(kid, vid, hash, reference_kid, side)

                new_root = await self.insert_root_from_merkle_blob(merkle_blob, store_id, status)
                return InsertResult(node_hash=hash, root=new_root)
```

**File:** chia/data_layer/data_store.py (L1449-1461)
```python
                        if reference_node_hash is None and side is None:
                            if enable_batch_autoinsert and reference_kid is None:
                                if key_hash_frequency[key_hashed] == 1 or (
                                    key_hash_frequency[key_hashed] == 2 and first_action[key_hashed] == "delete"
                                ):
                                    batch_keys_values.append((kid, vid))
                                    batch_hashes.append(hash)
                                    continue
                            if not merkle_blob.empty():
                                seed = leaf_hash(key=key, value=value)
                                reference_kid, side = self.get_reference_kid_side(merkle_blob, seed)

                        merkle_blob.insert(kid, vid, hash, reference_kid, side)
```

**File:** chia/data_layer/data_store.py (L1560-1591)
```python
    async def get_nodes_for_file(
        self,
        root: Root,
        node_hash: bytes32,
        store_id: bytes32,
        deltas_only: bool,
        delta_file_cache: DeltaFileCache,
        tree_nodes: list[SerializedNode],
    ) -> None:
        if deltas_only:
            if delta_file_cache.seen_previous_hash(node_hash):
                return

        raw_index = delta_file_cache.get_index(node_hash)
        raw_node = delta_file_cache.get_raw_node(raw_index)

        if isinstance(raw_node, chia_rs.datalayer.InternalNode):
            left_hash = delta_file_cache.get_hash_at_index(raw_node.left)
            right_hash = delta_file_cache.get_hash_at_index(raw_node.right)
            await self.get_nodes_for_file(root, left_hash, store_id, deltas_only, delta_file_cache, tree_nodes)
            await self.get_nodes_for_file(root, right_hash, store_id, deltas_only, delta_file_cache, tree_nodes)
            tree_nodes.append(SerializedNode(False, bytes(left_hash), bytes(right_hash)))
        elif isinstance(raw_node, chia_rs.datalayer.LeafNode):
            tree_nodes.append(
                SerializedNode(
                    True,
                    raw_node.key.to_bytes(),
                    raw_node.value.to_bytes(),
                )
            )
        else:
            raise Exception(f"Node is neither InternalNode nor TerminalNode: {raw_node}")
```

**File:** chia/data_layer/data_store.py (L1620-1645)
```python
    async def write_tree_to_file(
        self,
        root: Root,
        node_hash: bytes32,
        store_id: bytes32,
        deltas_only: bool,
        writer: BinaryIO,
    ) -> None:
        if node_hash == bytes32.zeros:
            return

        with log_exceptions(log=log, message="Error while getting merkle blob"):
            root_path = self.get_merkle_path(store_id=store_id, root_hash=root.node_hash)
        delta_file_cache = DeltaFileCache(root_path)

        if root.generation > 0:
            previous_root = await self.get_tree_root(store_id=store_id, generation=root.generation - 1)
            if previous_root.node_hash is not None:
                with log_exceptions(log=log, message="Error while getting previous merkle blob"):
                    previous_root_path = self.get_merkle_path(store_id=store_id, root_hash=previous_root.node_hash)
                delta_file_cache.load_previous_hashes(previous_root_path)

        tree_nodes: list[SerializedNode] = []

        await self.get_nodes_for_file(root, node_hash, store_id, deltas_only, delta_file_cache, tree_nodes)
        kv_ids = (
```

**File:** chia/data_layer/download_data.py (L69-105)
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
```

**File:** chia/_tests/core/data_layer/test_data_store.py (L611-627)
```python
@pytest.mark.anyio()
async def test_autoinsert_balances_from_scratch(data_store: DataStore, store_id: bytes32) -> None:
    random = Random()
    random.seed(100, version=2)
    hashes = []

    for i in range(2000):
        key = (i + 100).to_bytes(4, byteorder="big")
        value = (i + 200).to_bytes(4, byteorder="big")
        insert_result = await data_store.autoinsert(key, value, store_id, status=Status.COMMITTED)
        hashes.append(insert_result.node_hash)

    heights = {node_hash: len(await data_store.get_ancestors(node_hash, store_id)) for node_hash in hashes}
    too_tall = {hash: height for hash, height in heights.items() if height > 14}
    assert too_tall == {}
    assert 11 <= statistics.mean(heights.values()) <= 12

```

**File:** chia/_tests/core/data_layer/test_data_store.py (L1726-1765)
```python
@pytest.mark.anyio
@pytest.mark.parametrize("depth", [1000, 100_000])
async def test_insert_into_data_store_from_file_line_graph_depth(
    data_store: DataStore,
    store_id: bytes32,
    tmp_path: Path,
    depth: int,
) -> None:
    # Binary "line graph":
    #   1 -> leaf_1, 2
    #   2 -> leaf_2, 3
    #   ...
    #   depth -> leaf_depth, leaf_end
    #
    # This stresses depth/stack handling while staying strictly binary.
    #
    # Node IDs:
    # - internal nodes: 1..depth
    # - per-level leaf nodes: (depth+1)..(2*depth)
    # - final leaf: (2*depth+1)
    internal_first = 1
    internal_last = depth
    leaf_base = internal_last + 1
    leaf_end = (2 * depth) + 1

    edges: list[tuple[int, int]] = []
    for i in range(internal_first, internal_last):
        # Order matters: first edge is left child, second edge is right child.
        edges.append((i, leaf_base + (i - internal_first)))  # unique leaf for this internal node
        edges.append((i, i + 1))  # next internal node

    # last internal points to its leaf and a final leaf
    edges.append((internal_last, leaf_base + (internal_last - internal_first)))
    edges.append((internal_last, leaf_end))

    filename = tmp_path / f"line_graph_depth_{depth}.dat"
    root_hash = create_graph_util(filename, edges)

    with pytest.raises(chia_rs.datalayer.RecursionDepthExceededError):
        await data_store.insert_into_data_store_from_file(store_id, root_hash, filename)
```
