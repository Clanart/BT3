## Title
Unbounded `get_blocks` RPC range allows uncontrolled memory allocation via a single local RPC call - (File: chia/full_node/full_node_rpc_api.py)

## Summary
The full node's `/get_blocks` RPC endpoint accepts arbitrary `start`/`end` height parameters with no bound on the requested range size, then materializes every block in that range (including full generator bytes) into memory before serializing all of them to a JSON response. Unlike other similarly-shaped endpoints in this codebase (`get_coin_records`, `get_ips_after_timestamp`, `request_coin_state`), which enforce a `max_*_limit`/`max_items` cap before doing the underlying work, `get_blocks` has no such cap, mirroring the CWE-400 pattern described in the Tempo advisory (large caller-supplied limit -> large uncontrolled memory allocation).

## Finding Description
`get_blocks` reads `start` and `end` directly from the request, builds `block_range = range(start, end)` with no upper bound check on `end - start`, and passes the full range to `block_store.get_full_blocks_at`, then converts every returned `FullBlock` to its JSON dict representation and appends it to a Python list: [1](#0-0) 

Contrast this with the analogous, properly-hardened endpoints in the same RPC surface area:
- `WalletRpcApi.get_coin_records` explicitly rejects any `limit` above `max_get_coin_records_limit` and any filter list above `max_get_coin_records_filter_items` before touching the coin store: [2](#0-1) 
- The crawler's `get_ips_after_timestamp` caps `offset`/`limit` (default 10000) before slicing: [3](#0-2) 
- `request_coin_state` (peer wallet protocol) truncates `coin_ids` to `max_subscribe_response_items(peer)` before doing any DB work: [4](#0-3) 
- `CoinStore.get_coin_records_by_parent_ids` enforces a `max_items=50000` ceiling on the query itself: [5](#0-4) 

`get_blocks` has none of these protections — `start`/`end` are converted straight to `int()` and used to build the range with no maximum span check, no default cap, and no per-request byte/count ceiling before the full set of `FullBlock` objects (each of which can carry a full transactions generator) is loaded and JSON-serialized in one response.

## Impact Explanation
A caller of the full-node RPC can request an arbitrarily large height range (e.g., `start=0`, `end=<current tip>`), forcing the full node to pull every full block (including large transaction generators) for that entire range from the block store into memory simultaneously, then serialize the entire set to JSON in a single response. On a chain with a long history this can allocate a very large amount of memory and CPU in one call, which can degrade or crash the full node process — an availability impact on a service that other mempool/consensus operations (block validation, mempool processing) depend on. This matches the CWE-400 classification and impact profile of the reported Tempo issue (large query limit -> large allocation -> availability impact), scoped here to the "local RPC caller" actor class explicitly permitted by the validation rules.

## Likelihood Explanation
Any caller with access to the full node RPC endpoint (which does not require a signed spend bundle, just an RPC call) can trigger this with a single request containing large `start`/`end` values; no special privileges, timing, or races are required beyond generic RPC reachability. The main uncertainty is how RPC access is gated in this deployment (cert-based auth is typically required for the full node RPC), which limits the attack surface strictly to already-authorized local/RPC callers rather than arbitrary network peers — this is consistent with the "local RPC caller" actor type explicitly allowed by the validation rules.

## Recommendation
Add an explicit maximum span check (and/or a hard cap on the number of blocks/bytes returned per call) to `get_blocks` in `chia/full_node/full_node_rpc_api.py`, analogous to `max_get_coin_records_limit` in `wallet_rpc_api.py` or the `max_items` bound in `CoinStore.get_coin_records_by_parent_ids`. Reject (or clamp) requests where `end - start` exceeds a configured maximum (e.g., a few hundred/thousand blocks), and consider paginating large-range requests instead of building the full JSON list in a single response.

## Proof of Concept
1. Start a full node and farm a chain with a non-trivial number of blocks with generators (transaction blocks).
2. Call the RPC: `POST /get_blocks` with `{"start": 0, "end": <chain_height>}`.
3. Observe `get_blocks` in `chia/full_node/full_node_rpc_api.py` (lines 413-441) building `block_range` for the entire span, fetching every `FullBlock` via `block_store.get_full_blocks_at`, and converting each to a JSON dict with no limit enforced anywhere in the call path — resulting in memory/CPU consumption proportional to the full requested range in a single synchronous response.

### Citations

**File:** chia/full_node/full_node_rpc_api.py (L413-441)
```python
    async def get_blocks(self, request: dict[str, Any]) -> EndpointResult:
        if "start" not in request:
            raise RpcError.simple(RpcErrorCodes.NO_START_IN_REQUEST, "No start in request")
        if "end" not in request:
            raise RpcError.simple(RpcErrorCodes.NO_END_IN_REQUEST, "No end in request")
        exclude_hh = False
        if "exclude_header_hash" in request:
            exclude_hh = request["exclude_header_hash"]
        exclude_reorged = False
        if "exclude_reorged" in request:
            exclude_reorged = request["exclude_reorged"]

        start = int(request["start"])
        end = int(request["end"])
        block_range = []
        for a in range(start, end):
            block_range.append(uint32(a))
        blocks: list[FullBlock] = await self.service.block_store.get_full_blocks_at(block_range)
        json_blocks = []
        for block in blocks:
            hh: bytes32 = block.header_hash
            if exclude_reorged and self.service.blockchain.height_to_hash(block.height) != hh:
                # Don't include forked (reorged) blocks
                continue
            json = block.to_json_dict()
            if not exclude_hh:
                json["header_hash"] = hh.hex()
            json_blocks.append(json)
        return {"blocks": json_blocks}
```

**File:** chia/wallet/wallet_rpc_api.py (L2764-2780)
```python
    async def get_coin_records(self, request: GetCoinRecords) -> GetCoinRecordsResponse:

        if request.limit != uint32.MAXIMUM and request.limit > self.max_get_coin_records_limit:
            raise ValueError(f"limit of {self.max_get_coin_records_limit} exceeded: {request.limit}")

        for filter_name, filter in {
            "coin_id_filter": request.coin_id_filter,
            "puzzle_hash_filter": request.puzzle_hash_filter,
            "parent_coin_id_filter": request.parent_coin_id_filter,
            "amount_filter": request.amount_filter,
        }.items():
            if filter is None:
                continue
            if len(filter.values) > self.max_get_coin_records_filter_items:
                raise ValueError(
                    f"{filter_name} max items {self.max_get_coin_records_filter_items} exceeded: {len(filter.values)}"
                )
```

**File:** chia/seeder/crawler_rpc_api.py (L62-79)
```python
    async def get_ips_after_timestamp(self, _request: dict[str, Any]) -> EndpointResult:
        after = _request.get("after", None)
        if after is None:
            raise ValueError("`after` is required and must be a unix timestamp")

        offset = _request.get("offset", 0)
        limit = _request.get("limit", 10000)

        matched_ips: list[str] = []
        for ip, timestamp in self.service.best_timestamp_per_peer.items():
            if timestamp > after:
                matched_ips.append(ip)

        matched_ips.sort()

        return {
            "ips": matched_ips[offset : (offset + limit)],
            "total": len(matched_ips),
```

**File:** chia/full_node/full_node_api.py (L2151-2158)
```python
    async def request_coin_state(self, request: wallet_protocol.RequestCoinState, peer: WSChiaConnection) -> Message:
        max_items = self.max_subscribe_response_items(peer)
        max_subscriptions = self.max_subscriptions(peer)
        subs = self.full_node.subscriptions

        coin_ids = request.coin_ids
        if len(coin_ids) > max_items:
            coin_ids = coin_ids[:max_items]
```

**File:** chia/full_node/coin_store.py (L380-402)
```python
    async def get_coin_records_by_parent_ids(
        self,
        include_spent_coins: bool,
        parent_ids: list[bytes32],
        start_height: uint32 = uint32(0),
        end_height: uint32 = uint32((2**32) - 1),
        *,
        max_items: int = 50000,
    ) -> list[CoinRecord]:
        if len(parent_ids) == 0:
            return []

        coins: set[CoinRecord] = set()
        async with self.db_wrapper.reader_no_transaction() as conn:
            for batch in to_batches(parent_ids, SQLITE_MAX_VARIABLE_NUMBER):
                parent_ids_db: tuple[Any, ...] = tuple(batch.entries)
                async with conn.execute(
                    f"SELECT confirmed_index, spent_index, coinbase, puzzle_hash, coin_parent, amount, timestamp "
                    f"FROM coin_record WHERE coin_parent in ({'?,' * (len(batch.entries) - 1)}?) "
                    f"AND confirmed_index>=? AND confirmed_index<? "
                    f"{'' if include_spent_coins else 'AND spent_index <= 0'}"
                    " LIMIT ?",
                    (*parent_ids_db, start_height, end_height, max_items - len(coins)),
```
