import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 25
# todo: the path from https://github.com/kaiachain/kaia
SOURCE_REPO = "kaiachain/kaia"
# todo: the name of the repository
REPO_NAME = "kaia"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"


scope_files = [
    # =================================================================================
    # Transaction admission: pool, gas price floor, fee market, spam throttling
    # =================================================================================
    "blockchain/tx_pool.go",
    "blockchain/tx_list.go",
    "blockchain/tx_journal.go",
    "blockchain/tx_cacher.go",
    "blockchain/gaspool.go",
    "blockchain/spam_throttler.go",
    "node/cn/gasprice/gasprice.go",
    "node/cn/gasprice/feehistory.go",
    "params/kip71_config.go",
    "params/protocol_params.go",
    "params/computation_cost_params.go",
    "params/governance_params.go",
    "params/network_params.go",
    "params/denomination.go",
    "params/blob_config.go",
    "params/config.go",

    # =================================================================================
    # Kaia transaction types, signing, signature sets and fee delegation payloads
    # =================================================================================
    "blockchain/types/transaction.go",
    "blockchain/types/transaction_signing.go",
    "blockchain/types/tx_signature.go",
    "blockchain/types/tx_signatures.go",
    "blockchain/types/tx_internal_data.go",
    "blockchain/types/tx_internal_data_serializer.go",
    "blockchain/types/tx_internal_data_legacy.go",
    "blockchain/types/tx_internal_data_value_transfer.go",
    "blockchain/types/tx_internal_data_value_transfer_memo.go",
    "blockchain/types/tx_internal_data_account_creation.go",
    "blockchain/types/tx_internal_data_account_update.go",
    "blockchain/types/tx_internal_data_smart_contract_deploy.go",
    "blockchain/types/tx_internal_data_smart_contract_execution.go",
    "blockchain/types/tx_internal_data_cancel.go",
    "blockchain/types/tx_internal_data_chain_data_anchoring.go",
    "blockchain/types/tx_internal_data_fee_delegated_value_transfer.go",
    "blockchain/types/tx_internal_data_fee_delegated_value_transfer_with_ratio.go",
    "blockchain/types/tx_internal_data_fee_delegated_value_transfer_memo.go",
    "blockchain/types/tx_internal_data_fee_delegated_value_transfer_memo_with_ratio.go",
    "blockchain/types/tx_internal_data_fee_delegated_account_update.go",
    "blockchain/types/tx_internal_data_fee_delegated_account_update_with_ratio.go",
    "blockchain/types/tx_internal_data_fee_delegated_smart_contract_deploy.go",
    "blockchain/types/tx_internal_data_fee_delegated_smart_contract_deploy_with_ratio.go",
    "blockchain/types/tx_internal_data_fee_delegated_smart_contract_execution.go",
    "blockchain/types/tx_internal_data_fee_delegated_smart_contract_execution_with_ratio.go",
    "blockchain/types/tx_internal_data_fee_delegated_cancel.go",
    "blockchain/types/tx_internal_data_fee_delegated_cancel_with_ratio.go",
    "blockchain/types/tx_internal_data_fee_delegated_chain_data_anchoring.go",
    "blockchain/types/tx_internal_data_fee_delegated_chain_data_anchoring_with_ratio.go",
    "blockchain/types/tx_internal_data_ethereum_access_list.go",
    "blockchain/types/tx_internal_data_ethereum_dynamic_fee.go",
    "blockchain/types/tx_internal_data_ethereum_blob.go",
    "blockchain/types/tx_internal_data_ethereum_set_code.go",
    "blockchain/types/anchoring_data.go",

    # =================================================================================
    # Account model and AccountKey authorization (multisig weights, role separation)
    # =================================================================================
    "blockchain/types/account/account.go",
    "blockchain/types/account/account_common.go",
    "blockchain/types/account/account_serializer.go",
    "blockchain/types/account/externally_owned_account.go",
    "blockchain/types/account/smart_contract_account.go",
    "blockchain/types/account/legacy_account.go",
    "blockchain/types/accountkey/account_key.go",
    "blockchain/types/accountkey/account_key_serializer.go",
    "blockchain/types/accountkey/account_key_legacy.go",
    "blockchain/types/accountkey/account_key_public.go",
    "blockchain/types/accountkey/account_key_weighted_multi_sig.go",
    "blockchain/types/accountkey/account_key_role_based.go",
    "blockchain/types/accountkey/account_key_fail.go",
    "blockchain/types/accountkey/account_key_nil.go",
    "blockchain/types/accountkey/public_key.go",

    # =================================================================================
    # State transition, fee/burn accounting, block body validation and processing
    # =================================================================================
    "blockchain/state_transition.go",
    "blockchain/state_processor.go",
    "blockchain/state_prefetcher.go",
    "blockchain/block_validator.go",
    "blockchain/blockchain.go",
    "blockchain/headerchain.go",
    "blockchain/evm.go",
    "blockchain/error.go",
    "blockchain/types.go",
    "blockchain/blob_storage.go",
    "blockchain/types/block.go",
    "blockchain/types/receipt.go",
    "blockchain/types/log.go",
    "blockchain/types/bloom.go",
    "blockchain/types/derive_sha.go",
    "blockchain/types/derivesha/mux.go",
    "blockchain/types/derivesha/orig.go",
    "blockchain/types/derivesha/simple.go",
    "blockchain/types/derivesha/concat.go",
    "blockchain/types/contract_ref.go",

    # =================================================================================
    # EVM execution, gas and computation-cost metering, precompiles
    # =================================================================================
    "blockchain/vm/evm.go",
    "blockchain/vm/interpreter.go",
    "blockchain/vm/instructions.go",
    "blockchain/vm/jump_table.go",
    "blockchain/vm/gas.go",
    "blockchain/vm/gas_table.go",
    "blockchain/vm/memory.go",
    "blockchain/vm/memory_table.go",
    "blockchain/vm/stack.go",
    "blockchain/vm/stack_table.go",
    "blockchain/vm/contract.go",
    "blockchain/vm/contracts.go",
    "blockchain/vm/precompiles.go",
    "blockchain/vm/eips.go",
    "blockchain/vm/operations_acl.go",
    "blockchain/vm/analysis.go",
    "blockchain/vm/jumpdests.go",
    "blockchain/vm/common.go",
    "blockchain/vm/errors.go",
    "blockchain/vm/interface.go",
    "blockchain/vm/access_list_tracer.go",

    # =================================================================================
    # State database, storage trie, proofs and snapshot layers
    # =================================================================================
    "blockchain/state/statedb.go",
    "blockchain/state/state_object.go",
    "blockchain/state/state_object_encoder.go",
    "blockchain/state/journal.go",
    "blockchain/state/database.go",
    "blockchain/state/access_list.go",
    "blockchain/state/transient_storage.go",
    "blockchain/state/iterator.go",
    "blockchain/state/sync.go",
    "storage/statedb/trie.go",
    "storage/statedb/secure_trie.go",
    "storage/statedb/hasher.go",
    "storage/statedb/node.go",
    "storage/statedb/node_enc.go",
    "storage/statedb/encoding.go",
    "storage/statedb/proof.go",
    "storage/statedb/stacktrie.go",
    "storage/statedb/flat_trie.go",
    "storage/statedb/iterator.go",
    "storage/statedb/database.go",
    "storage/statedb/sync.go",
    "snapshot/snapshot.go",
    "snapshot/difflayer.go",
    "snapshot/disklayer.go",
    "snapshot/generate.go",
    "snapshot/journal.go",
    "snapshot/conversion.go",
    "snapshot/iterator.go",
    "snapshot/iterator_fast.go",
    "snapshot/iterator_binary.go",
    "snapshot/sort.go",

    # =================================================================================
    # Gasless module: ApproveTx/SwapTx bundle admission, lending and reimbursement
    # =================================================================================
    "kaiax/gasless/interface.go",
    "kaiax/gasless/config.go",
    "kaiax/gasless/impl/init.go",
    "kaiax/gasless/impl/getter.go",
    "kaiax/gasless/impl/builder.go",
    "kaiax/gasless/impl/execution.go",
    "kaiax/gasless/impl/tx_pool.go",
    "kaiax/gasless/impl/tx_counter.go",
    "kaiax/gasless/impl/api.go",
    "kaiax/gasless/impl/constant.go",
    "kaiax/gasless/impl/errors.go",

    # =================================================================================
    # Auction module: EIP-712 bids, bid pool, bundle building and settlement
    # =================================================================================
    "kaiax/auction/bid.go",
    "kaiax/auction/eip712.go",
    "kaiax/auction/config.go",
    "kaiax/auction/errors.go",
    "kaiax/auction/interface.go",
    "kaiax/auction/impl/bid_pool.go",
    "kaiax/auction/impl/builder.go",
    "kaiax/auction/impl/execution.go",
    "kaiax/auction/impl/getter.go",
    "kaiax/auction/impl/handler.go",
    "kaiax/auction/impl/api.go",
    "kaiax/auction/impl/init.go",

    # =================================================================================
    # Governance parameters that price and gate user transactions
    # =================================================================================
    "kaiax/gov/param.go",
    "kaiax/gov/paramset.go",
    "kaiax/gov/interface.go",
    "kaiax/gov/error.go",
    "kaiax/gov/impl/getter.go",
    "kaiax/gov/impl/execution.go",
    "kaiax/gov/impl/header.go",
    "kaiax/gov/impl/init.go",
    "kaiax/gov/impl/rewind.go",
    "kaiax/gov/headergov/gov.go",
    "kaiax/gov/headergov/vote.go",
    "kaiax/gov/headergov/history.go",
    "kaiax/gov/headergov/interface.go",
    "kaiax/gov/headergov/impl/getter.go",
    "kaiax/gov/headergov/impl/execution.go",
    "kaiax/gov/headergov/impl/header.go",
    "kaiax/gov/headergov/impl/schema.go",
    "kaiax/gov/headergov/impl/rewind.go",
    "kaiax/gov/contractgov/interface.go",
    "kaiax/gov/contractgov/impl/getter.go",
    "kaiax/gov/contractgov/impl/init.go",

    # =================================================================================
    # Staking, reward distribution, supply accounting and validator set effects
    # =================================================================================
    "kaiax/staking/staking_info.go",
    "kaiax/staking/interface.go",
    "kaiax/staking/p2p_staking_info.go",
    "kaiax/staking/impl/getter.go",
    "kaiax/staking/impl/execution.go",
    "kaiax/staking/impl/preload_buffer.go",
    "kaiax/staking/impl/schema.go",
    "kaiax/reward/spec.go",
    "kaiax/reward/config.go",
    "kaiax/reward/interface.go",
    "kaiax/reward/impl/getter.go",
    "kaiax/reward/impl/execution.go",
    "kaiax/reward/impl/blockstate.go",
    "kaiax/reward/impl/header.go",
    "kaiax/supply/total_supply.go",
    "kaiax/supply/interface.go",
    "kaiax/supply/impl/getter.go",
    "kaiax/supply/impl/execution.go",
    "kaiax/supply/impl/schema.go",
    "kaiax/valset/types.go",
    "kaiax/valset/address_set.go",
    "kaiax/valset/interface.go",
    "kaiax/valset/impl/getter_council.go",
    "kaiax/valset/impl/getter_demote.go",
    "kaiax/valset/impl/getter_proposers.go",
    "kaiax/valset/impl/getter_permissionless.go",
    "kaiax/valset/impl/getter_context.go",
    "kaiax/valset/impl/transition.go",
    "kaiax/valset/impl/transition_context.go",
    "kaiax/valset/impl/blockstate.go",
    "kaiax/randao/impl/getter.go",
    "kaiax/randao/impl/execution.go",

    # =================================================================================
    # System contracts reachable from user transactions
    # =================================================================================
    "blockchain/system/registry.go",
    "blockchain/system/addressbook_v2.go",
    "blockchain/system/auction.go",
    "blockchain/system/kip113.go",
    "blockchain/system/multicall.go",
    "blockchain/system/permissionless.go",
    "blockchain/system/proxy.go",
    "blockchain/system/rebalance.go",
    "blockchain/system/storage.go",
    "blockchain/system/util.go",
    "blockchain/system/constant.go",

    # =================================================================================
    # Public RPC entrypoints, argument marshalling and read paths
    # =================================================================================
    "api/tx_args.go",
    "api/api_kaia_transaction.go",
    "api/api_kaia_account.go",
    "api/api_kaia_blockchain.go",
    "api/api_kaia.go",
    "api/api_eth.go",
    "api/api_personal.go",
    "api/api_txpool.go",
    "api/api_debug.go",
    "api/api_debug_util.go",
    "api/addrlock.go",
    "api/backend.go",
    "node/cn/api_backend.go",
    "node/cn/state_accessor.go",
    "node/cn/filters/filter.go",
    "node/cn/filters/filter_system.go",
    "node/cn/filters/api_kaia_filter.go",
    "node/cn/tracers/api.go",

    # =================================================================================
    # Block assembly: transaction ordering, bundle placement and execution
    # =================================================================================
    "work/worker.go",
    "work/work.go",
    "work/execution.go",
    "work/builder/builder.go",
    "work/builder/bundle.go",
    "work/builder/tx_or_gen.go",

    # =================================================================================
    # Encoding, hashing and signature primitives on validation paths
    # =================================================================================
    "rlp/decode.go",
    "rlp/encode.go",
    "rlp/encbuffer.go",
    "rlp/raw.go",
    "rlp/typecache.go",
    "rlp/iterator.go",
    "crypto/crypto.go",
    "crypto/signature_cgo.go",
    "crypto/signature_nocgo.go",
    "common/types.go",
    "common/bytes.go",
    "common/big.go",
    "common/cache.go",
]


target_scopes = [
    "Critical. An unprivileged sender moves KAIA or tokens out of an account whose keys they do not hold, because AccountKey authorization is evaluated wrongly: weight and threshold summation in AccountKeyWeightedMultiSig, role selection between RoleTransaction/RoleAccountUpdate/RoleFeePayer in AccountKeyRoleBased, AccountKeyLegacy or AccountKeyNil fallback, key serialization in account_key_serializer, or duplicate/recovered-key handling in TxSignatures lets a transaction validate against a key set the owner never authorized.",
    "Critical. A fee payer or a sender is charged for a transaction they never authorized, because fee-delegated transaction handling binds the feePayer signature to a different payload than the one executed: SerializeForSignToBytes, SenderFeePayer, chain-id or tx-hash construction in transaction_signing, or feeRatio arithmetic in state_transition lets an attacker reuse or graft a feePayer signature onto another transaction and drain the payer's balance.",
    "Critical. Total KAIA supply or account balance is inflated or destroyed by an ordinary transaction, because gas refund, KIP-71 base-fee burn, fee-ratio splitting, or gas-price-vs-effective-price accounting in state_transition, together with reward minting in kaiax/reward and burn tracking in kaiax/supply/total_supply, credits more than it debits or double-counts a value, so balances and accounted supply diverge from conserved value.",
    "Critical. One transaction any user can submit makes honest nodes compute different state, receipt, or block results, because Kaia-specific execution diverges between paths: computation-cost metering and ErrOutOfComputation in the interpreter, hard-fork gating of precompiles and EIPs, DeriveSha implementation selection in derivesha/mux, receipt or log encoding, state journal revert in statedb, or re-execution differing from the prefetch/tracing path, causing a chain split or acceptance of an invalid block.",
    "Critical. A gasless user or a third party steals from the gasless swap flow, because ApproveTx/SwapTx pair validation, bundle atomicity, allowlist and token checks, per-sender limits in tx_counter, or lend-and-reimburse accounting in kaiax/gasless/impl lets an attacker have the swap router fund a transaction that never repays it, split a bundle so only the funding half executes, or push honest gasless bundles out of a block.",
    "Critical. An auction participant is robbed or the auction settlement is subverted, because EIP-712 domain, nonce, or target-tx binding in kaiax/auction/eip712.go and bid.go, replacement rules in bid_pool, bundle placement in builder, or payment and refund handling in execution lets an unprivileged bidder replay or steal another searcher's bid, have a bid included without paying, or displace the victim transaction the bid was bound to.",
    "High. An unprivileged staker or permissionless-validator applicant redirects block rewards or changes the validator set, because staking-amount collection in kaiax/staking, the minimum-staking demotion rule in getter_demote, council transitions in kaiax/valset/impl/transition.go, permissionless registration checks, or the KIP-82/KIP-226 proposer/staker/KIF/KEF split in kaiax/reward/spec.go attributes stake or reward to an address that did not earn it.",
    "High. An attacker's transactions permanently keep other users' valid transactions out of blocks, because nonce-gap, replacement (price bump), queue-to-pending promotion, gasless or bundle reservation, or spam-throttler classification in tx_pool, tx_list and work/builder lets cheap attacker transactions evict, pin, or starve honest transactions, or lets an attacker-invalid transaction be selected into a block and fail block validation.",
    "High. A client accepts forged account or storage state, because trie key encoding, node decoding, secure-trie preimage handling, or merkle/range proof generation and verification in storage/statedb and the snapshot layers can be driven by attacker-controlled contract storage keys and values to produce a proof for a slot never written, a wrong storage root, or a snapshot layer inconsistent with the trie it claims to mirror.",
    "High. A transaction executes with different parameters than the caller authorized, because argument defaulting and conversion in api/tx_args.go, Ethereum-transaction compatibility mapping in api_eth.go, sender or feePayer resolution in api_kaia_transaction.go, or account locking in addrlock.go lets an unprivileged RPC caller have a node sign, resubmit, or execute a transaction with a changed recipient, value, gas price, or signer.",
    "Critical/High blind spot. An unprivileged transaction sender, contract deployer, fee-delegation counterparty, gasless user, auction bidder, staker, or public-RPC caller abuses an assumption the protocol never wrote down: a value checked at pool admission and trusted as already-checked at execution, an account key or fee ratio re-read after the check that authorized it, a limit enforced on one transaction type but not on its fee-delegated, with-ratio, or Ethereum-typed twin, state carried across transaction, block, hard-fork, rewind, or cache boundaries that was only proven safe inside one of them, or an error path that commits partial state - yielding unauthorized value movement, supply divergence, reward redirection, or nodes that disagree on the chain.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one kaia target.

    ```
    target_file format:
    "'File Name: blockchain/state_transition.go -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact kaia target:

    {target_file}

    Project focus:
    kaia is the Kaia L1 node (EVM-compatible, Istanbul BFT). Focus only on what an ordinary account reaches: Kaia transaction types and fee delegation, AccountKey authorization and role separation, transaction-pool admission and KIP-71 fee pricing, state transition and gas/burn accounting, EVM and computation-cost metering, state trie and proofs, the gasless and auction modules, governance parameters that price user transactions, staking and KIP-82/KIP-226 reward distribution, system contracts, block assembly ordering, and public JSON-RPC entrypoints.

    Rules:
    * Treat `File Name:` as the exact file/package.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Go symbols (function, method, struct, field, constant) when possible.
    * Attacker is unprivileged only: any key holder who funds an account and submits signed transactions of any type, deploys and calls contracts, co-signs as sender or feePayer in a fee-delegated pair, submits a gasless ApproveTx/SwapTx bundle, submits an auction bid, stakes through public staking contracts, or calls public JSON-RPC. They sign only for their own keys.
    * Attacker is NOT a validator, proposer, governing node, node operator, host or DB owner, or holder of another user's key. Never assume a malicious peer, malicious node, malicious validator, p2p/gossip/sync/consensus-message attacker, leaked key, compromised host, non-default configuration, or social engineering.
    * Out of scope, never ask about: p2p protocol and peer handling, node discovery and bootnodes, block propagation or downloader/snap sync, Istanbul consensus message handling, network-level DoS, BLS/VDF/randao cryptography internals, CLI flags, metrics, dependencies.
    * Ignore test files, mocks, benchmarks, docs, generated code, and TOML/config-only findings.
    * Every question must describe a real transaction, bundle, bid, or RPC call an attacker actually submits through a valid entrypoint. No generic unbounded-allocation, memory-growth, cache-size, or resource-exhaustion speculation; no "what if the input is huge" without a concrete submitted transaction and a concrete broken invariant.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must target unauthorized value movement, balance or supply inflation, fee or fee-delegation abuse, gasless or auction settlement theft, reward redirection, state divergence between honest nodes, or acceptance of an invalid transaction or block.
    * Every question must be testable by a Go unit test, a state-transition or EVM test, a tx-pool test, or a blockchain/block-validation test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authorization is exact: an account is debited only when signatures satisfy its AccountKey for the correct role, over the exact serialized payload that executes.
    * Value is conserved: balances in equal balances out plus fees burned and rewards minted; accounted total supply matches real state.
    * Determinism holds: every honest node executing the same transaction in the same block reaches the same state root, receipts, gas, and computation cost, regardless of ordering, caching, prefetch, or tracing.
    * Pricing is honest: pool admission gas price, intrinsic gas, fee ratio, and base fee match what execution and block validation enforce.
    * Settlement is atomic: a fee-delegated transaction, a gasless bundle, an auction bid, or a staking/reward payout either completes as authorized or leaves no party short.
    * Liveness of valid users: no attacker transaction can permanently keep other users' valid transactions out of blocks or stop nodes from processing them.

    Each question must include:
    1. target function/method;
    2. attacker action (a concrete transaction, bundle, bid, or RPC call: type, fields, signatures);
    3. preconditions (accounts, balance, contracts, and keys the attacker owns);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: Go unit/state-transition/tx-pool/block-validation test PARAMETERS and assert AUTHORIZATION_EXACTNESS, VALUE_CONSERVATION, DETERMINISM, HONEST_PRICING, ATOMIC_SETTLEMENT, or USER_LIVENESS.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused kaia exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: any key holder who funds an account, submits signed transactions of any Kaia or Ethereum type, deploys and calls contracts, acts as sender or feePayer in a fee-delegated pair, submits a gasless bundle or an auction bid, stakes through public contracts, or calls public JSON-RPC. No validator, proposer, governing node, operator, host, DB, or foreign-key access.
- Reject malicious-peer, malicious-node, malicious-validator, p2p/gossip/sync/consensus-message, network-DoS, leaked-key, host-level, and misconfiguration-only paths.
- Reject discovery/bootnode, downloader/snap-sync, BLS/randao cryptography internals, CLI, metrics, dependency-only, and test/mock/docs/generated/config-only findings.
- Reject generic unbounded-allocation or resource-growth claims with no concrete submitted transaction and no broken invariant.
- This program pays High and Critical only. Focus on real chain impact: unauthorized value movement, balance or supply inflation, fee or fee-delegation abuse, gasless or auction settlement theft, reward redirection, state divergence between honest nodes, or acceptance of an invalid transaction or block.

## Validate
- Trace the exact reachable path from the attacker's transaction, bundle, bid, or RPC call into the affected function.
- Check whether signature and AccountKey validation, intrinsic gas and computation-cost limits, pool admission checks, hard-fork gating, or existing error handling already stop it.
- Confirm the path is reachable on current mainnet chain config and the active hard fork.
- Accept only concrete unauthorized value movement, supply divergence, settlement theft, reward redirection, state divergence, invalid block acceptance, or a lasting inability to process valid transactions.
- Require exact file/function support and a reproducible Go unit, state-transition, EVM, tx-pool, or block-validation PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker transaction inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and severity: Critical (loss of funds, supply inflation, consensus divergence, invalid block acceptance) or High (authorization bypass, state corruption, reward redirection, long-lived inability to process valid transactions)]

### Likelihood Explanation
[Preconditions, accounts and balance needed, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Go unit/state-transition/tx-pool/block-validation test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for kaia.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only analogs an unprivileged transaction sender, contract deployer, fee-delegation counterparty, gasless user, auction bidder, staker, or public-RPC caller can reach: Kaia transaction types and fee delegation, AccountKey authorization, pool admission and KIP-71 pricing, state transition and gas/burn accounting, EVM and computation-cost metering, state trie and proofs, gasless and auction modules, governance parameters, staking and reward distribution, system contracts, block assembly, or public RPC.
- Reject malicious-peer, malicious-node, malicious-validator, p2p/consensus-message, network-DoS, leaked-key, operator-only, discovery, downloader/snap-sync, BLS/randao cryptography, CLI, mocked-only paths, dependency-only bugs, and no-impact analogs.
- Medium , High and Critical only; no low, or resource-only analogs.

## Validate
- Map the bug class to the strongest reachable kaia path from a single submitted transaction, bundle, bid, or RPC call.
- Prove root cause with exact file/function support.
- Accept only concrete unauthorized value movement, supply inflation, fee or fee-delegation abuse, gasless or auction settlement theft, reward redirection, state divergence between honest nodes, or acceptance of an invalid transaction or block.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for kaia security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- This program pays High and Critical only; reject low, medium, informational, best-practice, and resource-only reports.
- Reject malicious-peer, malicious-node, malicious-validator, p2p/gossip/consensus-message, network-level DoS, discovery/bootnode, downloader/snap-sync, BLS/randao cryptography, SSL/cert, CLI, metrics, dependency-only, docs/style, generated-file, and test/mock/config-only issues.
- Reject if the exploit needs validator, proposer, governing-node, operator, host, database, or privileged-account access, another user's key, victim social engineering, a non-default configuration, or anything outside what an unprivileged account holder can put in a transaction, bundle, bid, or public RPC call.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged transaction sender, contract deployer, fee-delegation counterparty, gasless user, auction bidder, staker, or public-RPC caller, unless the claim proves escalation from that starting point.
- The final impact must map to an in-scope category: Critical - stealing or loss of funds, unauthorized or manipulated transactions, balance or total-supply inflation, fee or fee-delegation abuse that drains a payer, gasless or auction settlement theft, state divergence between honest nodes, or acceptance of an invalid transaction or block; High - AccountKey or RPC authorization bypass, corruption of account, trie, pool, staking, or reward state, reward or fee redirection, price or fee manipulation, or long-lived inability of honest nodes to process valid transactions.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken authorization, value-conservation, determinism, pricing, settlement, or user-liveness invariant.
3. Reachable exploit path: preconditions (attacker-owned accounts, balance, contracts) -> submitted transaction, bundle, bid, or RPC call -> trigger -> bad result.
4. Existing signature and AccountKey validation, intrinsic gas and computation-cost limits, pool admission checks, hard-fork gating, and error handling reviewed and shown insufficient.
5. Concrete in-scope High/Critical impact with realistic likelihood.
6. Reproducible proof path: Go unit PoC, state-transition or EVM test, tx-pool test, block-validation test, or exact steps against a local network.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary account holder trigger this with a transaction, bundle, bid, or public RPC call, without validator, operator, host, or foreign-key access?
- Does the code actually behave as claimed under current mainnet chain config and the active hard fork?
- Is the impact caused by this code, not by a malicious peer, validator, or dependency?
- Is the theft, inflation, divergence, or halt concrete rather than hypothetical?
- Would a Kaia triager on HackenProof accept the proof-of-concept?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and Kaia bounty category]

## Likelihood Explanation
[Attacker capability, accounts and balance required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or Go unit/state-transition/tx-pool/block-validation test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
