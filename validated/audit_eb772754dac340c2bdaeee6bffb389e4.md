Audit Report

## Title
`JsonRpcRequestProcessor::get_multiple_accounts` aborts the entire batch when a single account fails to encode - ([File: rpc/src/rpc.rs])

## Summary
`get_multiple_accounts` loops over the requested pubkeys and calls `get_encoded_account` for each, using `?` on the awaited result, which propagates any single per-account encoding error (e.g., an account too large to Base58-encode) as a hard failure for the entire RPC call. This means one problematic pubkey in a batch prevents the response for every other valid pubkey in the same request from being returned.

## Finding Description
The loop body in `get_multiple_accounts` is: [1](#0-0) 
which spawns a blocking task per pubkey calling `get_encoded_account`, and immediately uses `?` on the result, bubbling any `Err` out of the whole function instead of converting it into a per-item `None`/error marker. `get_encoded_account` internally calls `encode_account`, whose Base58 encoding path can fail when account data exceeds `MAX_BASE58_BYTES` and no compensating `dataSlice` is supplied, as demonstrated by the boundary-condition test `test_encode_account_does_not_throw_despite_account_and_dataslice_being_too_large_to_base58_encode_because_their_intersection_fits`, which only passes because the data slice happens to make the encoded output fit — implying the un-sliced/larger case returns `Err`. [2](#0-1) 

The existing regression test for this path, `test_rpc_get_multiple_accounts`, only exercises the success case (including a nonexistent account correctly mapped to `null`), and does not cover the scenario where one pubkey's encoding fails while others succeed: [3](#0-2) 

There is no per-pubkey error containment (e.g., catching the encode error and substituting `None`) in the loop, unlike the nonexistent-account case which is already naturally represented as `None` inside `get_encoded_account`/`encode_account`. The `?` operator causes the entire `for pubkey in pubkeys` loop, and therefore the entire `getMultipleAccounts` request, to fail as soon as one pubkey's encoding errors.

## Impact Explanation
This is a per-request RPC availability issue that affects only the single client that constructs the offending request, since `getMultipleAccounts` is a self-contained, stateless read-only call scoped to the requesting client — it does not affect other concurrent RPC clients' independent requests or validator consensus/state in any way. This falls into the "single-client low-rate RPC crash/degradation" category, at most causing that one specific multi-account query to return an error instead of a partial result. It does not cause fund loss, consensus halt, false execution/rooting, or corruption of any lamports/authority/nonce/receipt/slot/root/hash/account state — the account data structures themselves remain entirely unaffected; only the RPC response for that specific call fails.

## Likelihood Explanation
Feasible: a client can craft a `getMultipleAccounts` request that includes a pubkey known to be too large to Base58-encode without a data slice, alongside otherwise valid pubkeys. This requires only public RPC access and knowledge of the target account's size — no privileged access, malicious validator, or timing race is required.

## Recommendation
Modify `get_multiple_accounts` in `rpc/src/rpc.rs` to catch a per-pubkey `encode_account`/`get_encoded_account` error and substitute `None` for that specific entry instead of propagating it with `?`, so the rest of the batch's valid, encodable accounts are still returned.

## Proof of Concept
1. Store an account whose data length exceeds `MAX_BASE58_BYTES` without a compensating `dataSlice`, such that `encode_account(..., UiAccountEncoding::Base58, None)` returns `Err`.
2. Send a `getMultipleAccounts` request with `encoding: "base58"` listing this pubkey together with several normal, encodable pubkeys.
3. Observe that the loop in `get_multiple_accounts` (rpc/src/rpc.rs, lines 580-588) returns an error for the entire call via `?`, rather than a response array with `null`/error only for the problematic pubkey and correct results for the rest — this differs from the existing `test_rpc_get_multiple_accounts`, which only tests the all-succeed path.

### Citations

**File:** rpc/src/rpc.rs (L580-588)
```rust
        for pubkey in pubkeys {
            let bank = Arc::clone(&bank);
            accounts.push(
                self.runtime
                    .spawn_blocking(move || {
                        get_encoded_account(&bank, &pubkey, encoding, data_slice, None)
                    })
                    .await
                    .expect("rpc: get_encoded_account panicked")?,
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
