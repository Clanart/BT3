## Title
Object-code deployment derives its "unique per-publish" seed from `account::get_sequence_number`, which is stale/meaningless for orderless (nonce-based) accounts — ([File: aptos-move/framework/aptos-framework/sources/object_code_deployment.move])

## Summary
`object_code_deployment::publish` derives the address of the freshly-created code object from `object_seed()`, which is built from `account::get_sequence_number(publisher) + 1` [1](#0-0) . This mirrors the external report's root cause pattern: a value that is supposed to make a deployment address unique/unpredictable (the CREATE2 salt/bytecode-hash in the ZKsync report, the sequence number here) is computed from a source that does not reliably vary the way the code assumes.

## Finding Description
Aptos now supports orderless transactions, which use a `replay_protection_nonce` instead of an account sequence number for replay protection (`ReplayProtector::Nonce`) [2](#0-1) , validated via `nonce_validation::check_and_insert_nonce` rather than incrementing `Account.sequence_number` [3](#0-2) . For an account that only ever submits orderless (nonce-based) transactions, its on-chain `sequence_number` field never advances.

`object_code_deployment::publish` (the entry point invoked by `deploy-object`) hard-codes the address-uniqueness scheme around `account::get_sequence_number(publisher) + 1` [4](#0-3) . The CLI itself acknowledges this gap with an explicit TODO: *"Update this code to support stateless accounts that don't have a sequence number"* directly above the `DeployObjectCode::execute` implementation that pre-computes the object address via `get_sequence_number(...) + 1` [5](#0-4) . The type-level helper `create_object_code_deployment_address` used both on-chain and by the CLI/tests is likewise defined purely in terms of `creator_sequence_number` [6](#0-5) .

Consequently, for a stateless/orderless-only account, every call to `object_code_deployment::publish` computes the *same* `object_seed` (since `get_sequence_number` never changes), so `object::create_named_object` is asked to create an object at the identical derived address on every deploy — directly contradicting the module's own doc comment: *"This enables users to deploy modules to an object with a unique address each time they are published."* [7](#0-6) .

I was not able to fully verify, within the indexed portion of `object.move`, the exact abort/overwrite behavior of `create_named_object`/`move_to<ObjectCore>` when an object already exists at that deterministic address (the file's relevant sections were not returned by the search tools available). This is the key open question that determines the concrete blast radius:
- If creation aborts cleanly on collision, the impact is a **denial-of-service** for the `deploy-object` flow for orderless accounts (repeated deploys fail after the first).
- If instead the second `publish()` call is able to reuse the same object/signer as an already-existing `ManagingRefs`-owning object (e.g., due to a code path that doesn't verify the object didn't previously exist), the second deployment could silently coalesce into an `code::publish_package_txn` "upgrade" of the first package rather than a genuinely fresh object, which could violate publisher expectations about object independence/ownership. This deeper mechanism could not be confirmed from the retrieved code and needs direct inspection of `object.move`'s `create_named_object`, `create_object_address`, and `ObjectCore` creation logic (not available in the index results returned).

## Impact Explanation
If confirmed to cause address collision/overwrite rather than a clean abort, this would be a **publish-path code-safety issue**: an unprivileged, ordinary flow (simply calling `deploy-object` twice from a stateless account) could produce object-code-deployment addresses that do not have the uniqueness guarantee the module promises, potentially colliding two logically distinct deployments onto one object address. Even in the more benign case (clean abort), this is a functional break of the flagship object-code-deployment feature for the new orderless-transaction account model that Aptos is actively rolling out, which is a legitimate current-mainnet-relevant regression in the publish path. However, without confirming the exact on-chain behavior of `create_named_object` on collision, I cannot assert with certainty that this reaches the "unauthorized code replacement / ownership change" bar required by the Publish Impact Gate.

## Likelihood Explanation
This is highly likely to be reachable in practice as Aptos migrates users to orderless/nonce-based (stateless) accounts, and the CLI code already flags the exact gap via its own TODO comment. Any account that adopts orderless transactions for all activity (a design goal of AIP-based nonce/replay-protection work) and later uses `aptos move deploy-object` twice would hit this condition.

## Recommendation
`object_seed()` in `object_code_deployment.move` should not rely solely on `account::get_sequence_number`. It should incorporate a value guaranteed to be unique per invocation regardless of replay-protector type — e.g., the transaction's actual replay-protector (sequence number *or* nonce, whichever was used) exposed to the Move layer, or a monotonically incrementing per-account deployment counter stored in a dedicated resource, so the seed's uniqueness does not silently degrade to a constant when an account is orderless. The CLI's `DeployObjectCode`/`UpgradeObjectPackage` address-prediction logic (`create_object_code_deployment_address`) needs the equivalent fix so predicted and actual addresses continue to match for stateless accounts, exactly as the in-code TODO already flags.

## Proof of Concept
Not fully constructible from the indexed code alone. Conceptual PoC:
1. Create/use an Aptos account that exclusively signs orderless transactions (`ReplayProtector::Nonce`), so its `Account.sequence_number` stays fixed (e.g., at 0).
2. Call `aptos_framework::object_code_deployment::publish` for package A. `object_seed` resolves to a seed built from `sequence_number + 1` (constant).
3. Call `publish` again for package B from the same account. Because `account::get_sequence_number` has not advanced, `object_seed` is identical to step 2's, so `object::create_named_object` is asked to create an object at the same derived address as package A's object.
4. Observe whether: (a) the transaction aborts (DoS on repeat use of `deploy-object` for orderless accounts), or (b) the deployment is misrouted into the first object, silently coalescing two independent deployments. Step 4 requires direct inspection of `object.move`'s object-creation internals, which was not available in this investigation, so the exact outcome could not be confirmed here — a Devin session with full repo access would be needed to trace `create_named_object` → `ObjectCore` creation and settle whether this degrades to DoS only or to an actual address/ownership-collision bug.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L1-5)
```text
/// This module allows users to deploy, upgrade and freeze modules deployed to objects on-chain.
/// This enables users to deploy modules to an object with a unique address each time they are published.
/// This modules provides an alternative method to publish code on-chain, where code is deployed to objects rather than accounts.
/// This is encouraged as it abstracts the necessary resources needed for deploying modules,
/// along with the required authorization to upgrade and freeze modules.
```

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L80-104)
```text
    public entry fun publish(
        publisher: &signer,
        metadata_serialized: vector<u8>,
        code: vector<vector<u8>>,
    ) {
        let publisher_address = signer::address_of(publisher);
        let object_seed = object_seed(publisher_address);
        let constructor_ref = &object::create_named_object(publisher, object_seed);
        let code_signer = &constructor_ref.generate_signer();
        code::publish_package_txn(code_signer, metadata_serialized, code);

        event::emit(Publish { object_address: signer::address_of(code_signer), });

        move_to(code_signer, ManagingRefs {
            extend_ref: constructor_ref.generate_extend_ref(),
        });
    }

    inline fun object_seed(publisher: address): vector<u8> {
        let sequence_number = account::get_sequence_number(publisher) + 1;
        let seeds = vector[];
        seeds.append(bcs::to_bytes(&OBJECT_CODE_DEPLOYMENT_DOMAIN_SEPARATOR));
        seeds.append(bcs::to_bytes(&sequence_number));
        seeds
    }
```

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/session_id.rs (L53-58)
```rust
    OrderlessTxn {
        sender: AccountAddress,
        nonce: u64,
        expiration_time: u64,
        script_hash: Vec<u8>,
    },
```

**File:** aptos-move/framework/aptos-framework/sources/transaction_validation.move (L169-185)
```text
        // Check for replay protection
        match (replay_protector) {
            SequenceNumber(txn_sequence_number) => {
                check_for_replay_protection_regular_txn(
                    sender_address,
                    gas_payer_address,
                    txn_sequence_number,
                );
            },
            Nonce(nonce) => {
                check_for_replay_protection_orderless_txn(
                    sender_address,
                    nonce,
                    txn_expiration_time,
                );
            }
        };
```

**File:** aptos-move/cli/src/commands.rs (L1451-1494)
```rust
    // TODO[Ordereless]: Update this code to support stateless accounts that don't have a sequence number
    async fn execute(mut self) -> CliTypedResult<TransactionSummary> {
        let sender_address = self.txn_options.get_public_key_and_address()?.1;

        let chunked_publish_large_packages_module_address =
            if self.chunked_publish_option.chunked_publish {
                Some(
                    self.chunked_publish_option
                        .large_packages_module
                        .large_packages_module_address(&self.txn_options)
                        .await?,
                )
            } else {
                None
            };

        let sequence_number = if self.chunked_publish_option.chunked_publish {
            // Perform a preliminary build to determine the number of transactions needed for chunked publish mode.
            // This involves building the package with mock account address `0xcafe` to calculate the transaction count.
            let mock_object_address = AccountAddress::from_hex_literal("0xcafe").unwrap();
            self.move_options
                .add_named_address(self.address_name.clone(), mock_object_address.to_string());
            let package = build_package_options(
                &self.move_options,
                &self.included_artifacts_args,
                &self.env,
            )?;
            let mock_payloads: Vec<TransactionPayload> = create_chunked_publish_payloads(
                package,
                PublishType::AccountDeploy,
                None,
                chunked_publish_large_packages_module_address.unwrap(),
                self.chunked_publish_option.chunk_size,
            )?
            .payloads;
            let staging_tx_count = (mock_payloads.len() - 1) as u64;
            get_sequence_number(&self.txn_options.rest_client()?, sender_address).await?
                + staging_tx_count
                + 1
        } else {
            get_sequence_number(&self.txn_options.rest_client()?, sender_address).await? + 1
        };

        let object_address = create_object_code_deployment_address(sender_address, sequence_number);
```

**File:** types/src/object_address.rs (L9-17)
```rust
pub fn create_object_code_deployment_address(
    creator: AccountAddress,
    creator_sequence_number: u64,
) -> AccountAddress {
    let mut seed = vec![];
    seed.extend(bcs::to_bytes(OBJECT_CODE_DEPLOYMENT_DOMAIN_SEPARATOR).unwrap());
    seed.extend(bcs::to_bytes(&creator_sequence_number).unwrap());
    create_object_address(creator, &seed)
}
```
