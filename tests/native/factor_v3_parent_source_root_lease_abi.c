#ifndef F3_BROKER_TESTING
#define F3_BROKER_TESTING 1
#endif
#ifndef F3_BROKER_DISPOSABLE_TEST_MANIFEST
#define F3_BROKER_DISPOSABLE_TEST_MANIFEST 1
#endif

#define wmain f3_parent_source_disposable_abi_broker_wmain
#include "../../native/factor_v3_formal_native_broker/factor_v3_formal_native_broker.c"
#undef wmain

__declspec(dllexport) int f3_parent_source_abi_derive_attempt_key(
    const char *semantic_input_root_sha256,
    char *output,
    size_t output_capacity
) {
    if (output == NULL || output_capacity < 65u) {
        return 0;
    }
    return factor_v3_parent_source_derive_attempt_key_sha256(
        semantic_input_root_sha256,
        output
    );
}

__declspec(dllexport) int f3_parent_source_abi_derive_global_identity(
    const char *attempt_key_sha256,
    char *output,
    size_t output_capacity
) {
    if (output == NULL || output_capacity < 65u) {
        return 0;
    }
    return factor_v3_parent_source_derive_global_attempt_identity_sha256(
        attempt_key_sha256,
        output
    );
}

__declspec(dllexport) int f3_parent_source_abi_derive_root_policy(
    const wchar_t *canonical_root,
    char *output,
    size_t output_capacity
) {
    if (output == NULL || output_capacity < 65u) {
        return 0;
    }
    return factor_v3_parent_source_derive_root_policy_sha256(
        canonical_root,
        output
    );
}

__declspec(dllexport) int f3_parent_source_abi_acquire_status(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path,
    const char *run_spec_sha256,
    const char *attempt_key_sha256,
    const char *global_attempt_identity_sha256,
    unsigned int timeout_milliseconds
) {
    HANDLE lease = NULL;
    int acquired = factor_v3_parent_source_acquire_root_lease(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        run_spec_sha256,
        attempt_key_sha256,
        global_attempt_identity_sha256,
        timeout_milliseconds,
        &lease
    );
    if (acquired == 1 && lease != NULL
        && !factor_v3_parent_source_release_root_lease(lease)) {
        return 0;
    }
    return acquired;
}

typedef struct F3ParentSourceAbiWrongThreadRelease {
    HANDLE lease;
    int release_result;
} F3ParentSourceAbiWrongThreadRelease;

static DWORD WINAPI f3_parent_source_abi_wrong_thread_release_worker(
    LPVOID raw
) {
    F3ParentSourceAbiWrongThreadRelease *context =
        (F3ParentSourceAbiWrongThreadRelease *)raw;
    context->release_result = factor_v3_parent_source_release_root_lease(
        context->lease
    );
    return 0u;
}

__declspec(dllexport) int f3_parent_source_abi_wrong_thread_release_rejected(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path,
    const char *run_spec_sha256,
    const char *attempt_key_sha256,
    const char *global_attempt_identity_sha256
) {
    F3ParentSourceAbiWrongThreadRelease context;
    HANDLE worker = NULL;
    int acquired;
    int unchanged;
    context.lease = NULL;
    context.release_result = -1;
    acquired = factor_v3_parent_source_acquire_root_lease(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        run_spec_sha256,
        attempt_key_sha256,
        global_attempt_identity_sha256,
        2000u,
        &context.lease
    );
    if (acquired != 1 || context.lease == NULL) {
        return 0;
    }
    worker = CreateThread(
        NULL,
        0,
        f3_parent_source_abi_wrong_thread_release_worker,
        &context,
        0,
        NULL
    );
    if (worker == NULL || WaitForSingleObject(worker, 5000u) != WAIT_OBJECT_0) {
        if (worker != NULL) {
            CloseHandle(worker);
        }
        factor_v3_parent_source_release_root_lease(context.lease);
        return 0;
    }
    CloseHandle(worker);
    unchanged = factor_v3_parent_source_root_lease_unchanged(
        context.lease,
        run_spec_sha256,
        attempt_key_sha256,
        global_attempt_identity_sha256
    );
    return context.release_result == 0
        && unchanged
        && factor_v3_parent_source_release_root_lease(context.lease);
}

__declspec(dllexport) int f3_parent_source_abi_root_epoch_transition(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path,
    const char *run_spec_sha256,
    const char *attempt_key_sha256,
    const char *global_attempt_identity_sha256,
    unsigned int requested_action,
    unsigned int *observed_root_state,
    unsigned int *transition
) {
    HANDLE lease = NULL;
    int acquired = factor_v3_parent_source_acquire_root_lease(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        run_spec_sha256,
        attempt_key_sha256,
        global_attempt_identity_sha256,
        2000u,
        &lease
    );
    int accepted;
    if (acquired != 1 || lease == NULL) {
        return 0;
    }
    accepted = factor_v3_parent_source_lookup_root_epoch_transition(
        lease,
        run_spec_sha256,
        attempt_key_sha256,
        global_attempt_identity_sha256,
        requested_action,
        observed_root_state,
        transition
    );
    return factor_v3_parent_source_release_root_lease(lease) && accepted;
}

__declspec(dllexport) int f3_parent_source_abi_publish_run_receipt(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path,
    const char *run_spec_sha256,
    const char *attempt_key_sha256,
    const char *global_attempt_identity_sha256
) {
    HANDLE lease = NULL;
    int acquired = factor_v3_parent_source_acquire_root_lease(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        run_spec_sha256,
        attempt_key_sha256,
        global_attempt_identity_sha256,
        2000u,
        &lease
    );
    int accepted;
    if (acquired != 1 || lease == NULL) {
        return 0;
    }
    accepted = factor_v3_parent_source_publish_run_receipt(
        lease,
        run_spec_sha256,
        attempt_key_sha256,
        global_attempt_identity_sha256
    );
    return factor_v3_parent_source_release_root_lease(lease) && accepted;
}

__declspec(dllexport) int f3_parent_source_abi_publish_terminal_receipt(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path,
    const char *run_spec_sha256,
    const char *attempt_key_sha256,
    const char *global_attempt_identity_sha256
) {
    HANDLE lease = NULL;
    int acquired = factor_v3_parent_source_acquire_root_lease(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        run_spec_sha256,
        attempt_key_sha256,
        global_attempt_identity_sha256,
        2000u,
        &lease
    );
    int accepted;
    if (acquired != 1 || lease == NULL) {
        return 0;
    }
    accepted = factor_v3_parent_source_publish_terminal_receipt(
        lease,
        run_spec_sha256,
        attempt_key_sha256,
        global_attempt_identity_sha256
    );
    return factor_v3_parent_source_release_root_lease(lease) && accepted;
}
