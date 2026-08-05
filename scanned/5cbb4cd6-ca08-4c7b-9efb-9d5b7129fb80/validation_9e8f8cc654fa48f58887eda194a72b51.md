### Title
`JsonRpcRequestProcessor::get_multiple_accounts` aborts the entire batch when a single account fails to encode - ([File: rpc/src/rpc.rs])

### Summary
`getMultipleAccounts` iterates over the requested pubkeys and calls `get_encoded_account` for each one, using `?` to propagate any error immediately. Because encoding a single account can fail (e.g. Base58 encoding when the account data is too large to represent), one problematic pubkey in the list causes the whole request to error out, discarding the results for every other, perfectly valid account in the batch — the same broken-batch-invariant described in the Bond Protocol report, where one item's revert broke the entire `findMarketFor` loop.

### Finding Description
`get_multiple_accounts` loops over all requested pubkeys, spawns a blocking task per pubkey to fetch+encode the account via `get_encoded_account`, and uses `?` on the awaited result: [1](#0-0) 

`get_encoded_account` internally calls `encode_account`, which can return an `Err` (visible in the `account-decoder` crate's `MAX_BASE58_BYTES`/encoding-too-large error path) when the account's data cannot be represented in the requested encoding (most notably Base58, whose encoded size can exceed the RPC response size limit). This same error condition is what `test_encode_account_does_not_throw_despite_account_and_dataslice_being_too_large_to_base58_encode_because_their_intersection_fits` in the RPC test suite is specifically probing around: [2](#0-1) 

Unlike `getAccountInfo` (single account, error is expected/contained), `getMultipleAccounts` batches many independent accounts in one call. The `?` inside the `for pubkey in pubkeys` loop means that as soon as one account in the list triggers an encoding error, the loop stops and the JSON-RPC call returns an error for the entire request — none of the other, successfully encodable accounts are returned. This is structurally identical to the `BondAggregator.findMarketFor` bug: a per-item failure condition (there, `payoutFor` reverting on `maxPayout` exceeded; here, `encode_account` erroring on oversized Base58 data) breaks an entire loop meant to aggregate independent per-item results.

There is no guard in `get_multiple_accounts` that catches or skips a per-pubkey encoding failure — the `Result` is bubbled straight up via `?`.

### Impact Explanation
This is an unprivileged, remotely-triggerable RPC-availability degradation: any client can construct a `getMultipleAccounts` request mixing normal pubkeys with one pubkey whose account data is large enough to overflow the Base58 encoding limit (or otherwise trip `encode_account`'s error path), causing the entire request to fail for all callers of that same JSON-RPC node instance in that moment. Legitimate multi-account queries (e.g., wallets aggregating balances) can be starved of results whenever any single account in the batch happens to be un-encodable in the requested encoding, and a low-rate/low-cost request is sufficient to reproduce this every time, matching the "single-client low-rate RPC crash/degradation" impact category.

### Likelihood Explanation
High likelihood: the attacker only needs to know or create one large account (or one account whose owner-encoded size is incompatible with the chosen encoding) and include its pubkey among a `getMultipleAccounts` request — no special privileges, no malicious validator, and no timing race are required. The Base58 size limit is a normal, reachable condition for accounts with data close to `MAX_BASE58_BYTES`.

### Recommendation
Change `get_multiple_accounts` in `rpc/src/rpc.rs` to encode each account independently and convert a per-account encoding failure into `None`/`null` for that entry (mirroring how a missing account is already represented), rather than propagating the error with `?` and aborting the whole batch. This preserves per-item semantics: a single unencodable account should not prevent all other accounts in the same `getMultipleAccounts` call from being returned.

### Proof of Concept
1. Store an account whose data length, once Base58-encoded, exceeds the RPC encoding size limit (as exercised by the existing test `test_encode_account_does_not_throw_despite_account_and_dataslice_being_too_large_to_base58_encode_because_their_intersection_fits`, which shows the boundary condition around `MAX_BASE58_BYTES`) — but construct the request/config combination (e.g., without a compensating `dataSlice`) so that `encode_account` returns `Err` instead of `Ok`. [2](#0-1) 
2. Send a `getMultipleAccounts` request whose pubkey list contains this problematic pubkey along with several normal, encodable pubkeys.
3. Observe that `get_multiple_accounts`'s loop: [3](#0-2) 
propagates the `Err` via `?` on the first encountered failing pubkey, so the RPC call returns an error response instead of a response array containing results for the valid pubkeys and `null`/error only for the bad one — unlike the existing `test_rpc_get_multiple_accounts` test, which only exercises the "all accounts encode successfully" (including nonexistent → `null`) path: [4](#0-3)

### Citations

**File:** rpc/src/rpc.rs (L562-592)
```rust
    pub async fn get_multiple_accounts(
        &self,
        pubkeys: Vec<Pubkey>,
        config: Option<RpcAccountInfoConfig>,
    ) -> Result<RpcResponse<Vec<Option<UiAccount>>>> {
        let RpcAccountInfoConfig {
            encoding,
            data_slice,
            commitment,
            min_context_slot,
        } = config.unwrap_or_default();
        let bank = self.get_bank_with_config(RpcContextConfig {
            commitment,
            min_context_slot,
        })?;
        let encoding = encoding.unwrap_or(UiAccountEncoding::Base64);

        let mut accounts = Vec::with_capacity(pubkeys.len());
        for pubkey in pubkeys {
            let bank = Arc::clone(&bank);
            accounts.push(
                self.runtime
                    .spawn_blocking(move || {
                        get_encoded_account(&bank, &pubkey, encoding, data_slice, None)
                    })
                    .await
                    .expect("rpc: get_encoded_account panicked")?,
            );
        }
        Ok(new_response(&bank, accounts))
    }
```

**File:** rpc/src/rpc.rs (L5791-5813)
```rust
    #[test]
    fn test_encode_account_does_not_throw_despite_account_and_dataslice_being_too_large_to_base58_encode_because_their_intersection_fits()
     {
        let data = vec![42; MAX_BASE58_BYTES + 1];
        let pubkey = Pubkey::new_unique();
        let account = AccountSharedData::create_from_existing_shared_data(
            42,
            Arc::new(data),
            pubkey,
            false,
            0,
        );
        let result = encode_account(
            &account,
            &pubkey,
            UiAccountEncoding::Base58,
            Some(UiDataSliceConfig {
                length: MAX_BASE58_BYTES + 1,
                offset: 1,
            }),
        );
        assert!(result.is_ok());
    }
```

**File:** rpc/src/rpc.rs (L5815-5862)
```rust
    #[test]
    fn test_rpc_get_multiple_accounts() {
        let rpc = RpcHandler::start();
        let bank = rpc.working_bank();

        let non_existent_pubkey = Pubkey::new_unique();
        let pubkey = Pubkey::new_unique();
        let address = pubkey.to_string();
        let data = vec![1, 2, 3, 4, 5];
        let account = AccountSharedData::create_from_existing_shared_data(
            42,
            Arc::new(data.clone()),
            Pubkey::default(),
            false,
            0,
        );
        bank.store_account(&pubkey, &account);

        // Test 3 accounts, one empty, one non-existent, and one with data
        let request = create_test_request(
            "getMultipleAccounts",
            Some(json!([[
                rpc.mint_keypair.pubkey().to_string(),
                non_existent_pubkey.to_string(),
                address,
            ]])),
        );
        let result: RpcResponse<Value> = parse_success_result(rpc.handle_request_sync(request));
        let expected = json!([
            {
                "owner": "11111111111111111111111111111111",
                "lamports": TEST_MINT_LAMPORTS,
                "data": ["", "base64"],
                "executable": false,
                "rentEpoch": 0,
                "space": 0,
            },
            null,
            {
                "owner": "11111111111111111111111111111111",
                "lamports": 42,
                "data": [BASE64_STANDARD.encode(&data), "base64"],
                "executable": false,
                "rentEpoch": 0,
                "space": 5,
            }
        ]);
        assert_eq!(result.value, expected);
```
