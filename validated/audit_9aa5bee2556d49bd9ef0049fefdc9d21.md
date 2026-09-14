### Title
Full Node RPC `get_coin_records_by_puzzle_hash(es)` Returns Unlimited Coin Records, Enabling Memory/CPU Exhaustion DoS - (File: chia/full_node/coin_store.py)

### Summary
The full node RPC handlers `get_coin_records_by_puzzle_hash` and `get_coin_records_by_puzzle_hashes` fetch and JSON-serialize **every** matching `CoinRecord` for a given puzzle hash with no limit, offset, or pagination, unlike sibling coin-lookup methods in the same store which enforce a `max_items` cap with a SQL `LIMIT`.

### Finding Description
`CoinStore.get_coin_records_by_puzzle_hash` and `CoinStore.get_coin_records_by_puzzle_hashes` build a plain `SELECT ... WHERE puzzle_hash=?/IN(...)` query with no `LIMIT` clause and accumulate all matching rows into a `set()`/`list()` in memory: [1](#0-0) [2](#0-1) 

This is inconsistent with the other coin-lookup functions in the very same class — `get_coin_states_by_puzzle_hashes`, `get_coin_records_by_parent_ids`, and `get_coin_states_by_ids` — which all take a `max_items: int = 50000` keyword and append a SQL `LIMIT ?` to bound the result set: [3](#0-2) [4](#0-3) 

The full node RPC layer calls the unbounded methods directly and converts every returned record to a JSON dict before responding, with no server-side limit enforcement (contrast this with the wallet RPC's `get_coin_records`, which explicitly checks `request.limit` against `self.max_get_coin_records_limit` before querying): [5](#0-4) 

For comparison, the wallet RPC enforces a hard cap: [6](#0-5) 

Since creating coins to an arbitrary puzzle hash is a permissionless, low-cost operation (any spend bundle can include `CREATE_COIN` conditions), an attacker can cheaply accumulate an arbitrarily large number of coin records under one puzzle hash over time (dusting / address reuse), and any subsequent RPC call querying that puzzle hash will attempt to load and serialize the entire unbounded set in a single request/response cycle.

### Impact Explanation
A single `get_coin_records_by_puzzle_hash`/`get_coin_records_by_puzzle_hashes` RPC call against a puzzle hash holding a very large number of coin records forces the full node to execute an unbounded SQL fetch, materialize every row as a `CoinRecord`, and serialize all of them into a single JSON response. This can cause excessive memory allocation and CPU consumption in one request, degrading or crashing the full node RPC service — a transaction-processing/service-availability halt matching CWE-400, consistent with the reported Liferay bug class (no limit on objects returned from an API request).

### Likelihood Explanation
Populating a puzzle hash with a very large coin count requires only ordinary, permissionless coin creation via spend bundles (no privileged access needed), and the query itself is a normal, undocumented-as-restricted full node RPC call reachable by any local RPC caller (e.g., wallet software, Data Layer client, automation scripts) that has RPC access. No special conditions, timing, or race are required — likelihood is high once a target puzzle hash has accumulated sufficient coin volume (which can also occur organically through address reuse).

### Recommendation
Add a `max_items`/`limit` parameter (mirroring `get_coin_records_by_parent_ids` and `get_coin_states_by_ids`) with a SQL `LIMIT` clause to `get_coin_records_by_puzzle_hash` and `get_coin_records_by_puzzle_hashes` in `chia/full_node/coin_store.py`, and enforce a maximum result-size cap in `full_node_rpc_api.py`'s corresponding handlers, returning an error or truncated/paginated response when the cap is exceeded — consistent with the pattern already used by the wallet RPC's `get_coin_records`.

### Proof of Concept
1. Submit a sequence of ordinary spend bundles that each include `CREATE_COIN` conditions paying to the same target `puzzle_hash` (e.g., an address under attacker's control or any commonly reused address), repeated across many blocks until the puzzle hash accumulates a very large number of unspent/spent coin records (e.g., hundreds of thousands to millions).
2. As any RPC caller with access to the full node RPC, call `get_coin_records_by_puzzle_hash` (or `get_coin_records_by_puzzle_hashes`) with that puzzle hash and `include_spent_coins=true` and no height bounds.
3. Observe that `CoinStore.get_coin_records_by_puzzle_hash` (chia/full_node/coin_store.py:257-278) executes an unbounded query, loads all matching rows into memory, and `full_node_rpc_api.py` (694-711) serializes every record to JSON in the response — causing large memory/CPU spikes and potential service disruption, with no limit parameter available to the caller to bound the query.

### Citations

**File:** chia/full_node/coin_store.py (L257-278)
```python
    async def get_coin_records_by_puzzle_hash(
        self,
        include_spent_coins: bool,
        puzzle_hash: bytes32,
        start_height: uint32 = uint32(0),
        end_height: uint32 = uint32((2**32) - 1),
    ) -> list[CoinRecord]:
        coins = set()

        async with self.db_wrapper.reader_no_transaction() as conn:
            async with conn.execute(
                f"SELECT confirmed_index, spent_index, coinbase, puzzle_hash, "
                f"coin_parent, amount, timestamp FROM coin_record INDEXED BY coin_puzzle_hash WHERE puzzle_hash=? "
                f"AND confirmed_index>=? AND confirmed_index<? "
                f"{'' if include_spent_coins else 'AND spent_index <= 0'}",
                (puzzle_hash, start_height, end_height),
            ) as cursor:
                for row in await cursor.fetchall():
                    coin = self.row_to_coin(row)
                    spent_index = uint32(0) if row[1] <= 0 else uint32(row[1])
                    coins.add(CoinRecord(coin, row[0], spent_index, row[2] != 0, row[6]))
                return list(coins)
```

**File:** chia/full_node/coin_store.py (L280-307)
```python
    async def get_coin_records_by_puzzle_hashes(
        self,
        include_spent_coins: bool,
        puzzle_hashes: list[bytes32],
        start_height: uint32 = uint32(0),
        end_height: uint32 = uint32((2**32) - 1),
    ) -> list[CoinRecord]:
        if len(puzzle_hashes) == 0:
            return []

        coins = set()
        puzzle_hashes_db: tuple[Any, ...]
        puzzle_hashes_db = tuple(puzzle_hashes)

        async with self.db_wrapper.reader_no_transaction() as conn:
            async with conn.execute(
                f"SELECT confirmed_index, spent_index, coinbase, puzzle_hash, "
                f"coin_parent, amount, timestamp FROM coin_record INDEXED BY coin_puzzle_hash "
                f"WHERE puzzle_hash in ({'?,' * (len(puzzle_hashes) - 1)}?) "
                f"AND confirmed_index>=? AND confirmed_index<? "
                f"{'' if include_spent_coins else 'AND spent_index <= 0'}",
                (*puzzle_hashes_db, start_height, end_height),
            ) as cursor:
                for row in await cursor.fetchall():
                    coin = self.row_to_coin(row)
                    spent_index = uint32(0) if row[1] <= 0 else uint32(row[1])
                    coins.add(CoinRecord(coin, row[0], spent_index, row[2] != 0, row[6]))
                return list(coins)
```

**File:** chia/full_node/coin_store.py (L347-378)
```python
    async def get_coin_states_by_puzzle_hashes(
        self,
        include_spent_coins: bool,
        puzzle_hashes: set[bytes32],
        min_height: uint32 = uint32(0),
        *,
        max_items: int = 50000,
    ) -> set[CoinState]:
        if len(puzzle_hashes) == 0:
            return set()

        coins: set[CoinState] = set()
        async with self.db_wrapper.reader_no_transaction() as conn:
            for batch in to_batches(puzzle_hashes, SQLITE_MAX_VARIABLE_NUMBER):
                puzzle_hashes_db: tuple[Any, ...] = tuple(batch.entries)
                async with conn.execute(
                    f"SELECT confirmed_index, spent_index, coinbase, puzzle_hash, "
                    f"coin_parent, amount, timestamp FROM coin_record INDEXED BY coin_puzzle_hash "
                    f"WHERE puzzle_hash in ({'?,' * (len(batch.entries) - 1)}?) "
                    f"AND (confirmed_index>=? OR spent_index>=?)"
                    f"{'' if include_spent_coins else ' AND spent_index <= 0'}"
                    " LIMIT ?",
                    (*puzzle_hashes_db, min_height, min_height, max_items - len(coins)),
                ) as cursor:
                    row: sqlite3.Row
                    for row in await cursor.fetchall():
                        coins.add(self.row_to_coin_state(row))

                if len(coins) >= max_items:
                    break

        return coins
```

**File:** chia/full_node/coin_store.py (L380-411)
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
                ) as cursor:
                    async for row in cursor:
                        coin = self.row_to_coin(row)
                        spent_index = uint32(0) if row[1] <= 0 else uint32(row[1])
                        coins.add(CoinRecord(coin, row[0], spent_index, row[2] != 0, row[6]))
                if len(coins) >= max_items:
                    break

        return list(coins)
```

**File:** chia/full_node/full_node_rpc_api.py (L694-733)
```python
    async def get_coin_records_by_puzzle_hash(self, request: dict[str, Any]) -> EndpointResult:
        """
        Retrieves the coins for a given puzzlehash, by default returns unspent coins.
        """
        if "puzzle_hash" not in request:
            raise RpcError.simple(RpcErrorCodes.PUZZLE_HASH_NOT_IN_REQUEST, "Puzzle hash not in request")
        kwargs: dict[str, Any] = {"include_spent_coins": False, "puzzle_hash": hexstr_to_bytes(request["puzzle_hash"])}
        if "start_height" in request:
            kwargs["start_height"] = uint32(request["start_height"])
        if "end_height" in request:
            kwargs["end_height"] = uint32(request["end_height"])

        if "include_spent_coins" in request:
            kwargs["include_spent_coins"] = request["include_spent_coins"]

        coin_records = await self.service.coin_store.get_coin_records_by_puzzle_hash(**kwargs)

        return {"coin_records": [coin_record_dict_backwards_compat(cr.to_json_dict()) for cr in coin_records]}

    async def get_coin_records_by_puzzle_hashes(self, request: dict[str, Any]) -> EndpointResult:
        """
        Retrieves the coins for a given puzzlehash, by default returns unspent coins.
        """
        if "puzzle_hashes" not in request:
            raise RpcError.simple(RpcErrorCodes.PUZZLE_HASHES_NOT_IN_REQUEST, "Puzzle hashes not in request")
        kwargs: dict[str, Any] = {
            "include_spent_coins": False,
            "puzzle_hashes": [hexstr_to_bytes(ph) for ph in request["puzzle_hashes"]],
        }
        if "start_height" in request:
            kwargs["start_height"] = uint32(request["start_height"])
        if "end_height" in request:
            kwargs["end_height"] = uint32(request["end_height"])

        if "include_spent_coins" in request:
            kwargs["include_spent_coins"] = request["include_spent_coins"]

        coin_records = await self.service.coin_store.get_coin_records_by_puzzle_hashes(**kwargs)

        return {"coin_records": [coin_record_dict_backwards_compat(cr.to_json_dict()) for cr in coin_records]}
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
