#ifndef F3_BROKER_TESTING
#define F3_BROKER_TESTING 1
#endif
#ifndef F3_BROKER_DISPOSABLE_TEST_MANIFEST
#define F3_BROKER_DISPOSABLE_TEST_MANIFEST 1
#endif
#ifndef F3_PARENT_SOURCE_ROOT_LEASE_HARNESS_PARSER_ONLY
#define F3_PARENT_SOURCE_ROOT_LEASE_HARNESS_PARSER_ONLY 0
#endif

#define wmain f3_production_broker_wmain
#include "../../native/factor_v3_formal_native_broker/factor_v3_formal_native_broker.c"
#undef wmain

#define F3_PARENT_SOURCE_RUN_SPEC_SHA256 \
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
#define F3_PARENT_SOURCE_SEMANTIC_INPUT_ROOT_SHA256 \
    "89abcdef0123456789abcdef0123456789abcdef0123456789abcdef01234567"
#define F3_PARENT_SOURCE_ATTEMPT_KEY_SCHEMA \
    "factor-v3-parent-source-development-authority-attempt-key/v1"
#define F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256 \
    "d7b05c7a8eedfe55931520f6840c0e8bfbe883311e3d50f74f9071b4494eb93f"
#define F3_PARENT_SOURCE_GLOBAL_ATTEMPT_SCHEMA \
    "factor-v3-parent-source-global-attempt-identity/v1"
#define F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256 \
    "0d9a4e8c499eb74dc375427c9e9438bf9a3bc1a910f4df6a04ac9b3df6827fa0"
#define F3_PARENT_SOURCE_TAMPERED_SHA256 \
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

#define F3_PARENT_SOURCE_ROOT_STATE_EMPTY 0u
#define F3_PARENT_SOURCE_ROOT_STATE_CLAIMED 1u
#define F3_PARENT_SOURCE_ROOT_STATE_COMPLETED 2u
#define F3_PARENT_SOURCE_ROOT_STATE_VERIFY_CLAIMED 3u
#define F3_PARENT_SOURCE_ROOT_STATE_TERMINAL 4u
#define F3_PARENT_SOURCE_ACTION_RUN 1u
#define F3_PARENT_SOURCE_ACTION_VERIFY 2u
#define F3_PARENT_SOURCE_TRANSITION_START_RUN 1u
#define F3_PARENT_SOURCE_TRANSITION_REJECT 2u
#define F3_PARENT_SOURCE_TRANSITION_START_VERIFY 3u

#define F3_PARENT_SOURCE_LEASE_ERROR 0
#define F3_PARENT_SOURCE_LEASE_ACQUIRED 1
#define F3_PARENT_SOURCE_LEASE_CONTENDED 2
#define F3_PARENT_SOURCE_LEASE_BINDING_REJECTED 3

typedef enum F3ParentSourceHarnessCommand {
    F3_PARENT_SOURCE_HARNESS_INVALID = 0,
    F3_PARENT_SOURCE_HARNESS_SELF_CHECK,
    F3_PARENT_SOURCE_HARNESS_IDENTITY_BINDING,
    F3_PARENT_SOURCE_HARNESS_EPOCH_MATRIX,
    F3_PARENT_SOURCE_HARNESS_EPOCH_PROBE,
    F3_PARENT_SOURCE_HARNESS_EPOCH_STAGE_HOLD,
    F3_PARENT_SOURCE_HARNESS_LEASE_HOLD,
    F3_PARENT_SOURCE_HARNESS_LEASE_TRY,
    F3_PARENT_SOURCE_HARNESS_LEASE_TAMPER
} F3ParentSourceHarnessCommand;

#if !F3_PARENT_SOURCE_ROOT_LEASE_HARNESS_PARSER_ONLY
static int factor_v3_parent_source_derive_attempt_key_sha256(
    const char *semantic_input_root_sha256,
    char attempt_key_sha256[65]
);

static int factor_v3_parent_source_derive_global_attempt_identity_sha256(
    const char *attempt_key_sha256,
    char global_attempt_identity_sha256[65]
);

static int factor_v3_parent_source_acquire_root_lease(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path,
    const char *run_spec_sha256,
    const char *attempt_key_sha256,
    const char *global_attempt_identity_sha256,
    unsigned int timeout_milliseconds,
    HANDLE *lease
);

static int factor_v3_parent_source_root_lease_unchanged(
    HANDLE lease,
    const char *run_spec_sha256,
    const char *attempt_key_sha256,
    const char *global_attempt_identity_sha256
);

static int factor_v3_parent_source_release_root_lease(HANDLE lease);

static int factor_v3_parent_source_lookup_root_epoch_transition(
    HANDLE lease,
    const char *run_spec_sha256,
    const char *attempt_key_sha256,
    const char *global_attempt_identity_sha256,
    unsigned int requested_action,
    unsigned int *observed_root_state,
    unsigned int *transition
);

static int factor_v3_parent_source_publish_run_receipt(
    HANDLE lease,
    const char *run_spec_sha256,
    const char *attempt_key_sha256,
    const char *global_attempt_identity_sha256
);

static int factor_v3_parent_source_publish_terminal_receipt(
    HANDLE lease,
    const char *run_spec_sha256,
    const char *attempt_key_sha256,
    const char *global_attempt_identity_sha256
);
#endif

static F3ParentSourceHarnessCommand f3_parent_source_parse_command(
    int argc,
    wchar_t **argv
) {
    if (argc == 2 && wcscmp(argv[1], L"--self-check") == 0) {
        return F3_PARENT_SOURCE_HARNESS_SELF_CHECK;
    }
    if (argc == 2 && wcscmp(argv[1], L"--identity-binding") == 0) {
        return F3_PARENT_SOURCE_HARNESS_IDENTITY_BINDING;
    }
    if (argc == 5 && wcscmp(argv[1], L"--epoch-matrix") == 0) {
        return F3_PARENT_SOURCE_HARNESS_EPOCH_MATRIX;
    }
    if (argc == 6 && wcscmp(argv[1], L"--epoch-probe") == 0) {
        return F3_PARENT_SOURCE_HARNESS_EPOCH_PROBE;
    }
    if (argc == 7 && wcscmp(argv[1], L"--epoch-stage-hold") == 0) {
        return F3_PARENT_SOURCE_HARNESS_EPOCH_STAGE_HOLD;
    }
    if (argc == 7 && wcscmp(argv[1], L"--lease-hold") == 0) {
        return F3_PARENT_SOURCE_HARNESS_LEASE_HOLD;
    }
    if (argc == 5 && wcscmp(argv[1], L"--lease-try") == 0) {
        return F3_PARENT_SOURCE_HARNESS_LEASE_TRY;
    }
    if (argc == 8 && wcscmp(argv[1], L"--lease-tamper") == 0) {
        return F3_PARENT_SOURCE_HARNESS_LEASE_TAMPER;
    }
    return F3_PARENT_SOURCE_HARNESS_INVALID;
}

static int f3_parent_source_self_check(void) {
    wchar_t *self_check[] = {L"harness", L"--self-check"};
    wchar_t *identity[] = {L"harness", L"--identity-binding"};
    wchar_t *epoch[] = {
        L"harness",
        L"--epoch-matrix",
        L"C:\\fixture",
        L"C:\\fixture\\run-claim.json",
        L"C:\\fixture\\verify-claim.json"
    };
    wchar_t *hold[] = {
        L"harness",
        L"--lease-hold",
        L"C:\\fixture",
        L"C:\\fixture\\run-claim.json",
        L"C:\\fixture\\verify-claim.json",
        L"C:\\fixture\\ready",
        L"C:\\fixture\\release"
    };
    wchar_t *probe[] = {
        L"harness",
        L"--epoch-probe",
        L"C:\\fixture",
        L"C:\\fixture\\run-claim.json",
        L"C:\\fixture\\verify-claim.json",
        L"1"
    };
    wchar_t *try_lease[] = {
        L"harness",
        L"--lease-try",
        L"C:\\fixture",
        L"C:\\fixture\\run-claim.json",
        L"C:\\fixture\\verify-claim.json"
    };
    wchar_t *stage_hold[] = {
        L"harness",
        L"--epoch-stage-hold",
        L"C:\\fixture",
        L"C:\\fixture\\run-claim.json",
        L"C:\\fixture\\verify-claim.json",
        L"C:\\fixture\\ready",
        L"C:\\fixture\\release"
    };
    wchar_t *tamper[] = {
        L"harness",
        L"--lease-tamper",
        L"C:\\fixture",
        L"C:\\fixture\\attempts\\run.claim.json",
        L"C:\\fixture\\attempts\\verify.claim.json",
        L"C:\\alternate",
        L"C:\\outside\\run.claim.json",
        L"C:\\outside\\verify.claim.json"
    };
    wchar_t *invalid[] = {L"harness", L"--lease-hold"};

    if (f3_parent_source_parse_command(2, self_check)
            != F3_PARENT_SOURCE_HARNESS_SELF_CHECK
        || f3_parent_source_parse_command(2, identity)
            != F3_PARENT_SOURCE_HARNESS_IDENTITY_BINDING
        || f3_parent_source_parse_command(5, epoch)
            != F3_PARENT_SOURCE_HARNESS_EPOCH_MATRIX
        || f3_parent_source_parse_command(6, probe)
            != F3_PARENT_SOURCE_HARNESS_EPOCH_PROBE
        || f3_parent_source_parse_command(7, stage_hold)
            != F3_PARENT_SOURCE_HARNESS_EPOCH_STAGE_HOLD
        || f3_parent_source_parse_command(7, hold)
            != F3_PARENT_SOURCE_HARNESS_LEASE_HOLD
        || f3_parent_source_parse_command(5, try_lease)
            != F3_PARENT_SOURCE_HARNESS_LEASE_TRY
        || f3_parent_source_parse_command(8, tamper)
            != F3_PARENT_SOURCE_HARNESS_LEASE_TAMPER
        || f3_parent_source_parse_command(2, invalid)
            != F3_PARENT_SOURCE_HARNESS_INVALID) {
        return 0;
    }
    printf("DISPOSABLE_PARENT_SOURCE_HARNESS_SELF_CHECK_OK\n");
    return 1;
}

#if !F3_PARENT_SOURCE_ROOT_LEASE_HARNESS_PARSER_ONLY
static int f3_parent_source_identity_binding(void) {
    char observed_attempt_key[65] = {0};
    char observed_global_identity[65] = {0};

    if (!factor_v3_parent_source_derive_attempt_key_sha256(
            F3_PARENT_SOURCE_SEMANTIC_INPUT_ROOT_SHA256,
            observed_attempt_key
        )
        || strcmp(observed_attempt_key, F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256) != 0
        || !factor_v3_parent_source_derive_global_attempt_identity_sha256(
            observed_attempt_key,
            observed_global_identity
        )
        || strcmp(
            observed_global_identity,
            F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256
        ) != 0) {
        return 0;
    }
    printf(
        "DISPOSABLE_IDENTITY_BOUND=%s:%s:%s\n",
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        observed_attempt_key,
        observed_global_identity
    );
    return 1;
}

static int f3_parent_source_acquire(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path,
    const char *run_spec_sha256,
    const char *attempt_key_sha256,
    const char *global_attempt_identity_sha256,
    unsigned int timeout_milliseconds,
    HANDLE *lease
) {
    *lease = NULL;
    return factor_v3_parent_source_acquire_root_lease(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        run_spec_sha256,
        attempt_key_sha256,
        global_attempt_identity_sha256,
        timeout_milliseconds,
        lease
    );
}

static int f3_parent_source_transition_matches(
    HANDLE lease,
    unsigned int requested_action,
    unsigned int expected_root_state,
    unsigned int expected_transition
) {
    unsigned int observed_root_state = 999u;
    unsigned int observed = 0u;

    return factor_v3_parent_source_lookup_root_epoch_transition(
        lease,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256,
        requested_action,
        &observed_root_state,
        &observed
    ) && observed_root_state == expected_root_state
        && observed == expected_transition;
}

static int f3_parent_source_epoch_matrix(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path
) {
    HANDLE lease = NULL;
    int acquired = f3_parent_source_acquire(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256,
        2000u,
        &lease
    );
    int accepted;

    if (acquired != F3_PARENT_SOURCE_LEASE_ACQUIRED || lease == NULL) {
        return 0;
    }
    accepted = f3_parent_source_transition_matches(
        lease,
        F3_PARENT_SOURCE_ACTION_RUN,
        F3_PARENT_SOURCE_ROOT_STATE_EMPTY,
        F3_PARENT_SOURCE_TRANSITION_START_RUN
    ) && f3_parent_source_transition_matches(
        lease,
        F3_PARENT_SOURCE_ACTION_RUN,
        F3_PARENT_SOURCE_ROOT_STATE_CLAIMED,
        F3_PARENT_SOURCE_TRANSITION_REJECT
    ) && factor_v3_parent_source_publish_run_receipt(
        lease,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256
    ) && f3_parent_source_transition_matches(
        lease,
        F3_PARENT_SOURCE_ACTION_VERIFY,
        F3_PARENT_SOURCE_ROOT_STATE_COMPLETED,
        F3_PARENT_SOURCE_TRANSITION_START_VERIFY
    ) && f3_parent_source_transition_matches(
        lease,
        F3_PARENT_SOURCE_ACTION_RUN,
        F3_PARENT_SOURCE_ROOT_STATE_VERIFY_CLAIMED,
        F3_PARENT_SOURCE_TRANSITION_REJECT
    ) && factor_v3_parent_source_publish_terminal_receipt(
        lease,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256
    ) && f3_parent_source_transition_matches(
        lease,
        F3_PARENT_SOURCE_ACTION_RUN,
        F3_PARENT_SOURCE_ROOT_STATE_TERMINAL,
        F3_PARENT_SOURCE_TRANSITION_REJECT
    ) && f3_parent_source_transition_matches(
        lease,
        F3_PARENT_SOURCE_ACTION_VERIFY,
        F3_PARENT_SOURCE_ROOT_STATE_TERMINAL,
        F3_PARENT_SOURCE_TRANSITION_REJECT
    ) && factor_v3_parent_source_root_lease_unchanged(
        lease,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256
    );
    accepted = factor_v3_parent_source_release_root_lease(lease) && accepted;
    if (!accepted) {
        return 0;
    }
    printf("DISPOSABLE_ROOT_EPOCH_MATRIX=verified\n");
    return 1;
}

static int f3_parent_source_epoch_probe(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path,
    const wchar_t *requested_action_text
) {
    HANDLE lease = NULL;
    unsigned int observed_root_state = 999u;
    unsigned int transition = 0u;
    unsigned long parsed_action;
    wchar_t *end = NULL;
    int acquired;
    if (requested_action_text == NULL) {
        return 0;
    }
    parsed_action = wcstoul(requested_action_text, &end, 10);
    if (end == requested_action_text || end == NULL || *end != L'\0') {
        return 0;
    }
    acquired = f3_parent_source_acquire(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256,
        2000u,
        &lease
    );
    if (acquired != F3_PARENT_SOURCE_LEASE_ACQUIRED || lease == NULL) {
        return 0;
    }
    if (!factor_v3_parent_source_lookup_root_epoch_transition(
            lease,
            F3_PARENT_SOURCE_RUN_SPEC_SHA256,
            F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
            F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256,
            (unsigned int)parsed_action,
            &observed_root_state,
            &transition
        )) {
        factor_v3_parent_source_release_root_lease(lease);
        return 0;
    }
    if (!factor_v3_parent_source_release_root_lease(lease)) {
        return 0;
    }
    printf(
        "DISPOSABLE_ROOT_EPOCH_OBSERVED=%u:%u\n",
        observed_root_state,
        transition
    );
    return 1;
}

static int f3_parent_source_touch(const wchar_t *path) {
    HANDLE handle = CreateFileW(
        path,
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        NULL,
        CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        NULL
    );
    if (handle == INVALID_HANDLE_VALUE) {
        return 0;
    }
    return CloseHandle(handle) != 0;
}

static int f3_parent_source_wait_for_path(
    const wchar_t *path,
    unsigned int timeout_milliseconds
);

static int f3_parent_source_epoch_stage_hold(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path,
    const wchar_t *ready_path,
    const wchar_t *release_path
) {
    static const char staged_raw[] = "disposable interrupted epoch staging";
    HANDLE lease = NULL;
    HANDLE staging = INVALID_HANDLE_VALUE;
    wchar_t claim_directory[32768];
    wchar_t staging_path[32768];
    DWORD written = 0;
    int acquired = f3_parent_source_acquire(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256,
        2000u,
        &lease
    );
    if (acquired != F3_PARENT_SOURCE_LEASE_ACQUIRED
        || lease == NULL
        || !parent_directory(
            global_run_claim_path,
            claim_directory,
            sizeof(claim_directory) / sizeof(claim_directory[0])
        )
        || swprintf(
            staging_path,
            sizeof(staging_path) / sizeof(staging_path[0]),
            L"%ls\\.parent-source-epoch-staging-crash.tmp",
            claim_directory
        ) <= 0) {
        if (lease != NULL) {
            factor_v3_parent_source_release_root_lease(lease);
        }
        return 0;
    }
    staging = CreateFileW(
        staging_path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ,
        NULL,
        CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH,
        NULL
    );
    if (staging == INVALID_HANDLE_VALUE
        || !WriteFile(
            staging,
            staged_raw,
            (DWORD)(sizeof(staged_raw) - 1u),
            &written,
            NULL
        )
        || written != sizeof(staged_raw) - 1u
        || !FlushFileBuffers(staging)) {
        if (staging != INVALID_HANDLE_VALUE) {
            CloseHandle(staging);
        }
        DeleteFileW(staging_path);
        factor_v3_parent_source_release_root_lease(lease);
        return 0;
    }
    if (!CloseHandle(staging)) {
        DeleteFileW(staging_path);
        factor_v3_parent_source_release_root_lease(lease);
        return 0;
    }
    staging = INVALID_HANDLE_VALUE;
    if (!f3_parent_source_touch(ready_path)) {
        DeleteFileW(staging_path);
        factor_v3_parent_source_release_root_lease(lease);
        return 0;
    }
    if (!f3_parent_source_wait_for_path(release_path, 10000u)) {
        factor_v3_parent_source_release_root_lease(lease);
        return 0;
    }
    if (!DeleteFileW(staging_path)
        || !factor_v3_parent_source_release_root_lease(lease)) {
        return 0;
    }
    printf("DISPOSABLE_ROOT_EPOCH_STAGING_RELEASED=1\n");
    return 1;
}

static int f3_parent_source_wait_for_path(
    const wchar_t *path,
    unsigned int timeout_milliseconds
) {
    unsigned int waited = 0u;
    while (waited < timeout_milliseconds) {
        DWORD attributes = GetFileAttributesW(path);
        if (attributes != INVALID_FILE_ATTRIBUTES) {
            return 1;
        }
        Sleep(20u);
        waited += 20u;
    }
    return 0;
}

static int f3_parent_source_lease_hold(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path,
    const wchar_t *ready_path,
    const wchar_t *release_path
) {
    HANDLE lease = NULL;
    int acquired = f3_parent_source_acquire(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256,
        2000u,
        &lease
    );
    int unchanged;

    if (acquired != F3_PARENT_SOURCE_LEASE_ACQUIRED
        || lease == NULL
        || !f3_parent_source_touch(ready_path)) {
        if (lease != NULL) {
            factor_v3_parent_source_release_root_lease(lease);
        }
        return 0;
    }
    if (!f3_parent_source_wait_for_path(release_path, 10000u)) {
        factor_v3_parent_source_release_root_lease(lease);
        return 0;
    }
    unchanged = factor_v3_parent_source_root_lease_unchanged(
        lease,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256
    );
    if (!factor_v3_parent_source_release_root_lease(lease) || !unchanged) {
        return 0;
    }
    printf("DISPOSABLE_ROOT_LEASE_HELD=1\n");
    return 1;
}

static int f3_parent_source_lease_try(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path
) {
    HANDLE lease = NULL;
    int acquired = f3_parent_source_acquire(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256,
        200u,
        &lease
    );

    if (acquired == F3_PARENT_SOURCE_LEASE_CONTENDED) {
        printf("DISPOSABLE_ROOT_LEASE_CONTENDED=1\n");
        return 75;
    }
    if (acquired != F3_PARENT_SOURCE_LEASE_ACQUIRED
        || lease == NULL
        || !factor_v3_parent_source_root_lease_unchanged(
            lease,
            F3_PARENT_SOURCE_RUN_SPEC_SHA256,
            F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
            F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256
        )) {
        if (lease != NULL) {
            factor_v3_parent_source_release_root_lease(lease);
        }
        return 0;
    }
    if (!factor_v3_parent_source_release_root_lease(lease)) {
        return 0;
    }
    printf("DISPOSABLE_ROOT_LEASE_ACQUIRED=1\n");
    return 1;
}

static int f3_parent_source_rejects_binding(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path,
    const char *run_spec_sha256,
    const char *attempt_key_sha256,
    const char *global_attempt_identity_sha256
) {
    HANDLE lease = NULL;
    int acquired = f3_parent_source_acquire(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        run_spec_sha256,
        attempt_key_sha256,
        global_attempt_identity_sha256,
        200u,
        &lease
    );
    if (lease != NULL) {
        factor_v3_parent_source_release_root_lease(lease);
    }
    return acquired == F3_PARENT_SOURCE_LEASE_BINDING_REJECTED;
}

static int f3_parent_source_lease_tamper(
    const wchar_t *global_attempt_ledger_root,
    const wchar_t *global_run_claim_path,
    const wchar_t *global_verify_claim_path,
    const wchar_t *alternate_global_attempt_ledger_root,
    const wchar_t *outside_run_claim_path,
    const wchar_t *outside_verify_claim_path
) {
    HANDLE lease = NULL;
    int acquired = f3_parent_source_acquire(
        global_attempt_ledger_root,
        global_run_claim_path,
        global_verify_claim_path,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256,
        2000u,
        &lease
    );
    int unchanged_rejects;

    if (acquired != F3_PARENT_SOURCE_LEASE_ACQUIRED || lease == NULL) {
        return 0;
    }
    unchanged_rejects = !factor_v3_parent_source_root_lease_unchanged(
        lease,
        F3_PARENT_SOURCE_TAMPERED_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256
    ) && !factor_v3_parent_source_root_lease_unchanged(
        lease,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_TAMPERED_SHA256,
        F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256
    ) && !factor_v3_parent_source_root_lease_unchanged(
        lease,
        F3_PARENT_SOURCE_RUN_SPEC_SHA256,
        F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
        F3_PARENT_SOURCE_TAMPERED_SHA256
    );
    if (!factor_v3_parent_source_release_root_lease(lease)
        || !unchanged_rejects
        || !f3_parent_source_rejects_binding(
            global_attempt_ledger_root,
            global_run_claim_path,
            global_verify_claim_path,
            F3_PARENT_SOURCE_TAMPERED_SHA256,
            F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
            F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256
        )
        || !f3_parent_source_rejects_binding(
            global_attempt_ledger_root,
            global_run_claim_path,
            global_verify_claim_path,
            F3_PARENT_SOURCE_RUN_SPEC_SHA256,
            F3_PARENT_SOURCE_TAMPERED_SHA256,
            F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256
        )
        || !f3_parent_source_rejects_binding(
            global_attempt_ledger_root,
            global_run_claim_path,
            global_verify_claim_path,
            F3_PARENT_SOURCE_RUN_SPEC_SHA256,
            F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
            F3_PARENT_SOURCE_TAMPERED_SHA256
        )
        || !f3_parent_source_rejects_binding(
            alternate_global_attempt_ledger_root,
            global_run_claim_path,
            global_verify_claim_path,
            F3_PARENT_SOURCE_RUN_SPEC_SHA256,
            F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
            F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256
        )
        || !f3_parent_source_rejects_binding(
            global_attempt_ledger_root,
            outside_run_claim_path,
            outside_verify_claim_path,
            F3_PARENT_SOURCE_RUN_SPEC_SHA256,
            F3_PARENT_SOURCE_ATTEMPT_KEY_SHA256,
            F3_PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SHA256
        )) {
        return 0;
    }
    printf("DISPOSABLE_ROOT_LEASE_TAMPER_REJECTED=3\n");
    return 1;
}
#endif

int wmain(int argc, wchar_t **argv) {
    F3ParentSourceHarnessCommand command = f3_parent_source_parse_command(argc, argv);

    if (command == F3_PARENT_SOURCE_HARNESS_SELF_CHECK) {
        return f3_parent_source_self_check() ? 0 : 70;
    }
#if F3_PARENT_SOURCE_ROOT_LEASE_HARNESS_PARSER_ONLY
    if (command != F3_PARENT_SOURCE_HARNESS_INVALID) {
        printf("DISPOSABLE_CONTRACT_UNAVAILABLE=%d\n", (int)command);
        return 90;
    }
#else
    if (command == F3_PARENT_SOURCE_HARNESS_IDENTITY_BINDING) {
        return f3_parent_source_identity_binding() ? 0 : 71;
    }
    if (command == F3_PARENT_SOURCE_HARNESS_EPOCH_MATRIX) {
        return f3_parent_source_epoch_matrix(argv[2], argv[3], argv[4]) ? 0 : 72;
    }
    if (command == F3_PARENT_SOURCE_HARNESS_EPOCH_PROBE) {
        return f3_parent_source_epoch_probe(
            argv[2], argv[3], argv[4], argv[5]
        ) ? 0 : 77;
    }
    if (command == F3_PARENT_SOURCE_HARNESS_EPOCH_STAGE_HOLD) {
        return f3_parent_source_epoch_stage_hold(
            argv[2], argv[3], argv[4], argv[5], argv[6]
        ) ? 0 : 78;
    }
    if (command == F3_PARENT_SOURCE_HARNESS_LEASE_HOLD) {
        return f3_parent_source_lease_hold(
            argv[2], argv[3], argv[4], argv[5], argv[6]
        ) ? 0 : 73;
    }
    if (command == F3_PARENT_SOURCE_HARNESS_LEASE_TRY) {
        int result = f3_parent_source_lease_try(argv[2], argv[3], argv[4]);
        return result == 1 ? 0 : (result == 75 ? 75 : 74);
    }
    if (command == F3_PARENT_SOURCE_HARNESS_LEASE_TAMPER) {
        return f3_parent_source_lease_tamper(
            argv[2], argv[3], argv[4], argv[5], argv[6], argv[7]
        ) ? 0 : 76;
    }
#endif
    fwprintf(stderr, L"disposable parent-source harness command rejected\n");
    return 64;
}
